# Copyright 2017, Inderpreet Singh, All rights reserved.

from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from webtest import TestApp

from tests.integration.test_web.test_web_app import BaseTestWebApp
from controller import Controller, ControllerPersist
from web.handler.controller import ControllerHandler
from model import ModelFile
from web import WebAppBuilder


class _PathPairIdentityMatchStr(str):
    def __new__(cls, value: str, match_value: str):
        obj = str.__new__(cls, value)
        obj._match_value = match_value
        return obj

    def __eq__(self, other):
        return other == self._match_value or str.__eq__(self, other)

    __hash__ = str.__hash__


class TestControllerHandler(BaseTestWebApp):
    def setUp(self):
        super().setUp()
        self.context.config.lftp.local_path = self.temp_dir
        self.web_app_builder.controller_handler = ControllerHandler(self.controller, local_path=self.temp_dir)
        self.web_app = self.web_app_builder.build()
        self.test_app = self.build_browser_test_app(auth_secret=self.integration_admin_secret)
        self.controller.get_model_files = MagicMock(return_value=[])
        self.controller.get_model_file_command_identities = MagicMock(
            side_effect=lambda: tuple(
                (model_file.file_id, model_file.name, model_file.path_pair_id)
                for model_file in self.controller.get_model_files()
            )
        )

    def test_queue_timeout_is_derived_from_the_normal_authority_fence(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(125.0, ControllerHandler._queue_action_timeout())
        with patch.dict("os.environ", {"INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS": "600"}):
            self.assertEqual(125.0, ControllerHandler._queue_action_timeout())

    def _build_real_authority_controller_composition(self):
        config = self.context.config
        lftp_config = config.lftp
        for name, value in {
            "remote_address": "remote.server.com",
            "remote_username": "user",
            "remote_password": "password",
            "remote_port": 22,
            "remote_path": "/remote",
            "remote_path_to_scan_script": "/scanfs",
            "local_path": self.temp_dir,
            "use_ssh_key": False,
            "num_max_parallel_downloads": 1,
            "num_max_parallel_files_per_download": 1,
            "num_max_connections_per_root_file": 1,
            "num_max_connections_per_dir_file": 1,
            "num_max_total_connections": 1,
            "use_temp_file": False,
            "rate_limit": None,
            "net_socket_buffer": "8M",
            "staging_path": None,
            "protocol": "sftp",
            "remote_ftp_port": 21,
            "ftp_ssl_verify_certificate": True,
        }.items():
            setattr(lftp_config, name, value)
        for name, value in {
            "interval_ms_remote_scan": 1,
            "interval_ms_local_scan": 1,
            "interval_ms_downloading_scan": 1,
            "extract_path": "/extract",
            "use_local_path_as_extract_path": False,
        }.items():
            setattr(config.controller, name, value)
        config.general.verbose = False
        config.autoqueue.enabled = False
        config.autoqueue.patterns_only = False
        config.autoqueue.auto_extract = False
        self.context.args.local_path_to_scanfs = "/scanfs"

        with patch("controller.controller.create_transfer_backend") as create_backend:
            backend = MagicMock()
            backend.backend_name = "sync-test"
            create_backend.return_value = backend
            controller = Controller(self.context, ControllerPersist())

        files = {}
        for name in ("authority-success", "authority-expiry", "authority-pending"):
            model_file = ModelFile(name, True)
            model_file.path_pair_id = "pair-a"
            model_file.remote_size = 10
            model_file.remote_has_transferable_content = True
            files[model_file.file_id] = model_file

        controller._Controller__model = MagicMock()
        controller._Controller__model.get_file.side_effect = lambda file_id: files[file_id]
        controller._Controller__model_builder = MagicMock()
        controller._Controller__model_builder.has_unresolved_staging_collision.return_value = False
        controller._Controller__model_builder.get_terminalizable_staging_collision_file_ids.return_value = set()
        controller._Controller__model_builder.get_remote_resume_source_identity.return_value = None
        controller._Controller__path_pairs_by_id = {
            "pair-a": SimpleNamespace(remote_path="/remote", local_path="/local"),
        }
        controller._Controller__path_pair_staging_paths = {"pair-a": "/local/incomplete"}
        controller._Controller__local_scan_process = MagicMock()
        controller._Controller__remote_scan_process = MagicMock()
        controller._Controller__local_scan_process.session_token = "local-test-session"
        controller._Controller__remote_scan_process.session_token = "remote-test-session"
        controller._Controller__local_scan_process.generation = 0
        controller._Controller__remote_scan_process.generation = 0
        controller._Controller__scan_authority_tokens = {"local": {}, "remote": {}}
        controller._Controller__reconciled_local_path_pair_ids = set()
        controller._Controller__reconciled_remote_path_pair_ids = set()
        controller._Controller__lftp = MagicMock()
        controller._Controller__lftp.backend_name = "sync-test"
        controller._Controller__lftp.net_socket_buffer = ""
        controller.get_model_file_command_identities = MagicMock(
            side_effect=lambda: tuple(
                (model_file.file_id, model_file.name, model_file.path_pair_id)
                for model_file in files.values()
            )
        )
        controller.get_model_summary = MagicMock(return_value={"model_version": 1})
        controller.record_queue_http_wait_trace = MagicMock()
        controller.get_model_files_and_add_listener = MagicMock(return_value=[])
        controller.remove_model_listener = MagicMock()

        builder = WebAppBuilder(
            self.context, controller, self.auto_queue_persist, self.auth_store,
        )
        builder.controller_handler = ControllerHandler(controller, local_path=self.temp_dir)
        test_app = TestApp(
            builder.build(),
            extra_environ={
                "HTTP_AUTHORIZATION": "Bearer {}".format(self.integration_admin_secret),
            },
        )
        return controller, files, test_app

    def test_real_controller_and_handler_share_bounded_authority_and_http_waits(self):
        """TestApp cannot model a disconnected client; timeout does not cancel an intent."""
        self.assertEqual(125.0, ControllerHandler._queue_action_timeout())
        controller, files, test_app = self._build_real_authority_controller_composition()
        issued_commands = []
        command_enqueued = Event()
        original_queue_command = controller.queue_command

        def capture_queue_command(command):
            issued_commands.append(command)
            original_queue_command(command)
            command_enqueued.set()

        controller.queue_command = capture_queue_command
        clock = [0.0]

        def queue_url(model_file):
            return "/server/command/queue/{}?file_id={}".format(
                model_file.name, quote(model_file.file_id, safe=""),
            )

        def reset_authority():
            clock[0] = 0.0
            controller._Controller__scan_authority_tokens = {"local": {}, "remote": {}}
            controller._Controller__reconciled_local_path_pair_ids.clear()
            controller._Controller__reconciled_remote_path_pair_ids.clear()
            command_enqueued.clear()

        def issue_queue_request(model_file, responses, key):
            responses[key] = test_app.post(queue_url(model_file), expect_errors=True)

        with patch("controller.controller.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(ControllerHandler, "_QUEUE_ACTION_TIMEOUT", 1.0):
            success_file = files['["pair-a","authority-success"]']
            reset_authority()
            responses = {}
            success_thread = Thread(
                target=issue_queue_request,
                args=(success_file, responses, "success"),
            )
            success_thread.start()
            self.assertTrue(command_enqueued.wait(timeout=1.0))
            controller._Controller__process_commands()
            self.assertEqual(120.0, controller._Controller__deferred_queue_intents[
                success_file.file_id
            ].rescan_deadline_monotonic)
            clock[0] = 96.0
            controller._Controller__scan_authority_tokens = {
                "local": {"pair-a": ("local-test-session", 1)},
                "remote": {"pair-a": ("remote-test-session", 1)},
            }
            controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
            controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
            controller._Controller__process_commands()
            success_thread.join(timeout=1.0)

            self.assertFalse(success_thread.is_alive())
            self.assertEqual(200, responses["success"].status_code)
            self.assertEqual(1, controller._Controller__lftp.queue.call_count)

            expiry_file = files['["pair-a","authority-expiry"]']
            reset_authority()
            expiry_responses = {}
            expiry_thread = Thread(
                target=issue_queue_request,
                args=(expiry_file, expiry_responses, "expiry"),
            )
            expiry_thread.start()
            self.assertTrue(command_enqueued.wait(timeout=1.0))
            controller._Controller__process_commands()
            expiry_intent = controller._Controller__deferred_queue_intents[expiry_file.file_id]
            expiry_command = next(
                command for command in issued_commands if command.filename == expiry_file.file_id
            )
            expiry_callback = expiry_command.callbacks[0]
            expiry_callback.on_failure = MagicMock(wraps=expiry_callback.on_failure)
            expiry_intent.rescan_deadline_grace_consumed = True
            clock[0] = 120.0
            controller._Controller__process_commands()
            expiry_thread.join(timeout=1.0)

            self.assertFalse(expiry_thread.is_alive())
            self.assertEqual(409, expiry_responses["expiry"].status_code)
            self.assertNotIn(expiry_file.file_id, controller._Controller__deferred_queue_intents)
            expiry_callback.on_failure.assert_called_once_with(
                "Queue preflight cancelled: initial_scan_authority_deadline", 409,
            )

            clock[0] = 121.0
            controller._Controller__scan_authority_tokens = {
                "local": {"pair-a": ("local-test-session", 1)},
                "remote": {"pair-a": ("remote-test-session", 1)},
            }
            controller._Controller__reconciled_local_path_pair_ids.add("pair-a")
            controller._Controller__reconciled_remote_path_pair_ids.add("pair-a")
            controller._Controller__process_commands()
            self.assertEqual(1, controller._Controller__lftp.queue.call_count)
            self.assertFalse(expiry_callback.success)
            expiry_callback.on_failure.assert_called_once()

            pending_file = files['["pair-a","authority-pending"]']
            reset_authority()
            pending_responses = {}
            pending_thread = Thread(
                target=issue_queue_request,
                args=(pending_file, pending_responses, "pending"),
            )
            pending_thread.start()
            self.assertTrue(command_enqueued.wait(timeout=1.0))
            controller._Controller__process_commands()
            summary_response = test_app.get("/server/model/v1/summary")
            pending_thread.join(timeout=1.0)

            self.assertFalse(pending_thread.is_alive())
            self.assertEqual(200, summary_response.status_code)
            self.assertEqual({"model_version": 1}, summary_response.json)
            self.assertEqual(504, pending_responses["pending"].status_code)
            self.assertIn(pending_file.file_id, controller._Controller__deferred_queue_intents)

    def test_full_scan_requires_admin_authentication(self):
        self.controller.request_full_scan = MagicMock(return_value={
            "schema": "full_scan.v1",
            "accepted": True,
            "rejected": False,
            "operation": "full_scan",
            "local_generation": 4,
            "remote_generation": 5,
            "dispatch": "both",
            "reason": "enqueued",
        })

        self.context.config.general.disable_browser_auth = True
        unauthenticated = self.build_browser_test_app()
        response = unauthenticated.post(
            "/server/command/full_scan", expect_errors=True,
        )
        self.assertEqual(401, response.status_int)
        self.controller.request_full_scan.assert_not_called()

        write_secret = self.auth_store.create_api_key("integration-write", ["write"])["secret"]
        write_app = TestApp(
            self.web_app,
            extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(write_secret)},
        )
        response = write_app.post(
            "/server/command/full_scan", expect_errors=True,
        )
        self.assertEqual(403, response.status_int)
        self.controller.request_full_scan.assert_not_called()

    def test_full_scan_returns_fixed_authenticated_envelope(self):
        expected = {
            "schema": "full_scan.v1",
            "accepted": True,
            "rejected": False,
            "operation": "full_scan",
            "local_generation": 4,
            "remote_generation": 5,
            "dispatch": "both",
            "reason": "enqueued",
        }
        self.controller.request_full_scan = MagicMock(return_value=expected)

        response = self.test_app.post("/server/command/full_scan")

        self.assertEqual(202, response.status_int)
        self.assertEqual("application/json", response.content_type)
        self.assertEqual(expected, response.json)
        self.controller.request_full_scan.assert_called_once_with()

    def test_full_scan_rejects_inconsistent_success_envelopes(self):
        base = {
            "schema": "full_scan.v1",
            "accepted": True,
            "rejected": False,
            "operation": "full_scan",
            "local_generation": 4,
            "remote_generation": 5,
            "dispatch": "both",
            "reason": "enqueued",
        }
        for field, value in (
                ("accepted", False),
                ("dispatch", "local"),
                ("reason", "partial_dispatch"),
        ):
            with self.subTest(field=field):
                envelope = dict(base)
                envelope[field] = value
                self.controller.request_full_scan = MagicMock(return_value=envelope)
                response = self.test_app.post(
                    "/server/command/full_scan", expect_errors=True,
                )
                self.assertEqual(500, response.status_int)
                self.assertEqual(envelope, response.json)

    def test_full_scan_returns_truthful_fixed_failure_envelope(self):
        expected = {
            "schema": "full_scan.v1",
            "accepted": False,
            "rejected": True,
            "operation": "full_scan",
            "local_generation": 9,
            "remote_generation": 10,
            "dispatch": "local",
            "reason": "partial_dispatch",
        }
        self.controller.request_full_scan = MagicMock(return_value=expected)

        response = self.test_app.post(
            "/server/command/full_scan", expect_errors=True,
        )

        self.assertEqual(500, response.status_int)
        self.assertEqual("application/json", response.content_type)
        self.assertEqual(set(expected), set(response.json))
        self.assertEqual(expected, response.json)
        self.assertNotIn("private", response.text)
        self.controller.request_full_scan.assert_called_once_with()

    def test_full_scan_gate_off_returns_fixed_rejection_envelope(self):
        expected = {
            "schema": "full_scan.v1",
            "accepted": False,
            "rejected": True,
            "operation": "full_scan",
            "local_generation": None,
            "remote_generation": None,
            "dispatch": "none",
            "reason": "debug_gate_off",
        }
        self.controller.request_full_scan = MagicMock(return_value=expected)

        with patch.dict("os.environ", {"INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS": "599"}):
            response = self.test_app.post(
                "/server/command/full_scan", expect_errors=True,
            )

        self.assertEqual(403, response.status_int)
        self.assertEqual("application/json", response.content_type)
        self.assertEqual(
            {
                "schema", "accepted", "rejected", "operation",
                "local_generation", "remote_generation", "dispatch", "reason",
            },
            set(response.json),
        )
        self.assertEqual(expected, response.json)
        self.assertNotIn("private", response.text)
        self.controller.request_full_scan.assert_called_once_with()

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

    def test_queue_resolution_uses_immutable_identities_without_full_model_snapshot(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.get_model_files.side_effect = AssertionError(
            "command resolution must not request deep-copied model files"
        )
        self.controller.get_model_file_command_identities.side_effect = None
        self.controller.get_model_file_command_identities.return_value = (
            ("movie-id", "movie.mkv", None),
        )
        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post(
            "/server/command/queue/movie.mkv?file_id=movie-id"
        )

        self.assertEqual(200, response.status_code)
        self.controller.get_model_files.assert_not_called()
        self.controller.get_model_file_command_identities.assert_called_once_with()
        command = self.controller.queue_command.call_args.args[0]
        self.assertEqual("movie-id", command.filename)

    def test_nested_serialized_file_id_resolves_for_manual_actions(self):
        def side_effect(command: Controller.Command):
            command.callbacks[0].on_success()

        child_id = '["movies","release/nested/episode.bin"]'
        self.controller.get_model_file_command_identities.side_effect = None
        self.controller.get_model_file_command_identities.return_value = (
            (child_id, "episode.bin", "movies"),
        )
        self.controller.queue_command = MagicMock(side_effect=side_effect)
        encoded_name = quote(quote("episode.bin", safe=""), safe="")
        encoded_id = quote(child_id, safe="")
        requests = (
            (Controller.Command.Action.QUEUE, "post", "queue"),
            (Controller.Command.Action.STOP, "post", "stop"),
            (Controller.Command.Action.DELETE_LOCAL, "delete", "delete_local"),
            (Controller.Command.Action.DELETE_REMOTE, "delete", "delete_remote"),
        )

        for action, method, endpoint in requests:
            with self.subTest(action=action):
                response = getattr(self.test_app, method)(
                    "/server/command/{}/{}?file_id={}".format(endpoint, encoded_name, encoded_id)
                )
                self.assertEqual(200, response.status_code)
                command = self.controller.queue_command.call_args.args[0]
                self.assertEqual(action, command.action)
                self.assertEqual(child_id, command.filename)

    def test_queue_resolution_accepts_unique_name_without_identity(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.get_model_files.side_effect = AssertionError(
            "command resolution must not request deep-copied model files"
        )
        self.controller.get_model_file_command_identities.side_effect = None
        self.controller.get_model_file_command_identities.return_value = (
            ("movie-id", "movie.mkv", None),
        )
        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post("/server/command/queue/movie.mkv")

        self.assertEqual(200, response.status_code)
        self.controller.get_model_files.assert_not_called()
        command = self.controller.queue_command.call_args.args[0]
        self.assertEqual("movie.mkv", command.filename)

    def test_queue_rejects_ascii_control_characters(self):
        for control_char in ("\n", "\r", "\t", "\x01", "\x7f"):
            with self.subTest(control_char=repr(control_char)):
                self.controller.queue_command = MagicMock()
                uri = quote(quote("bad{}name".format(control_char), safe=""), safe="")

                response = self.test_app.post("/server/command/queue/" + uri, expect_errors=True)

                self.assertEqual(400, response.status_code)
                self.assertEqual("Invalid file path", response.text)
                self.controller.queue_command.assert_not_called()

    def test_queue_rejects_control_characters_in_identity_resolved_file_id(self):
        self.controller.queue_command = MagicMock()
        bad_file_id = quote("bad\x01id", safe="")

        response = self.test_app.post("/server/command/queue/safe-name?file_id={}".format(bad_file_id), expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_queue_rejects_control_characters_in_identity_resolved_model_name(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file("bad\x01name", "safe-file-id")
        ]

        response = self.test_app.post("/server/command/queue/safe-name?file_id=safe-file-id", expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_queue_rejects_control_characters_in_raw_path_pair_id(self):
        self.controller.queue_command = MagicMock()
        bad_path_pair_id = quote("bad\x01id", safe="")

        response = self.test_app.post(
            "/server/command/queue/safe-name?path_pair_id={}".format(bad_path_pair_id),
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_queue_accepts_path_pair_identity(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=side_effect)
        self.controller.get_model_files.return_value = [
            self.__model_file("dup", "[\"movies\",\"dup\"]", "movies")
        ]

        response = self.test_app.post("/server/command/queue/dup?path_pair_id=movies")

        self.assertEqual(200, response.status_code)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("[\"movies\",\"dup\"]", command.filename)

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

    def test_retry_move_requires_exact_encoded_file_id(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_success()

        file_id = '["movies","File One.mkv"]'
        self.controller.get_model_files.return_value = [
            self.__model_file("File One.mkv", file_id, "movies")
        ]
        self.controller.queue_command = MagicMock(side_effect=side_effect)
        uri = quote(quote("File One.mkv", safe=""), safe="")

        response = self.test_app.post(
            "/server/command/retry_move/{}?file_id={}".format(uri, quote(file_id, safe=""))
        )

        self.assertEqual(200, response.status_code)
        command = self.controller.queue_command.call_args.args[0]
        self.assertEqual(Controller.Command.Action.RETRY_MOVE, command.action)
        self.assertEqual(file_id, command.filename)

    def test_retry_move_rejects_missing_ambiguous_and_control_identity(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file("dup", '["movies","dup"]', "movies"),
            self.__model_file("dup", '["tv","dup"]', "tv"),
        ]

        missing = self.test_app.post("/server/command/retry_move/dup", expect_errors=True)
        ambiguous = self.test_app.post(
            "/server/command/retry_move/dup?file_id={}".format(quote('["other","dup"]', safe="")),
            expect_errors=True,
        )
        control = self.test_app.post(
            "/server/command/retry_move/dup?file_id={}".format(quote("bad\x01id", safe="")),
            expect_errors=True,
        )

        self.assertEqual(400, missing.status_code)
        self.assertEqual(400, ambiguous.status_code)
        self.assertEqual(400, control.status_code)
        self.controller.queue_command.assert_not_called()

    def test_retry_move_returns_generic_controller_failure(self):
        def side_effect(cmd: Controller.Command):
            cmd.callbacks[0].on_failure("Final move failed", 500)

        self.controller.get_model_files.return_value = [self.__model_file("movie.mkv", "movie.mkv")]
        self.controller.queue_command = MagicMock(side_effect=side_effect)

        response = self.test_app.post(
            "/server/command/retry_move/movie.mkv?file_id=movie.mkv",
            expect_errors=True,
        )

        self.assertEqual(500, response.status_code)
        self.assertEqual("Final move failed", response.text)

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

    def test_delete_local_rejects_base_directory_target(self):
        self.controller.queue_command = MagicMock()
        uri = quote(quote(".", safe=""), safe="")

        response = self.test_app.delete("/server/command/delete_local/"+uri, expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_delete_local_rejects_null_byte_filename(self):
        self.controller.queue_command = MagicMock()
        uri = quote(quote("bad\x00name", safe=""), safe="")

        response = self.test_app.delete("/server/command/delete_local/"+uri, expect_errors=True)

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
        self.test_app = self.build_browser_test_app(auth_secret=self.integration_admin_secret)
        self.controller.queue_command = MagicMock()

        response = self.test_app.delete("/server/command/delete_local/test1", expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_queue_remains_allowed_when_local_path_root_is_unavailable(self):
        self.web_app_builder.controller_handler = ControllerHandler(self.controller, local_path=None)
        self.web_app = self.web_app_builder.build()
        self.test_app = self.build_browser_test_app(auth_secret=self.integration_admin_secret)

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

    def test_queue_rejects_control_characters_in_path_pair_id_resolved_file_id(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file("safe-name", "bad\x01id", "movies")
        ]

        response = self.test_app.post("/server/command/queue/safe-name?path_pair_id=movies", expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_queue_rejects_control_characters_in_path_pair_id_resolved_model_name(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file(_PathPairIdentityMatchStr("bad\x01name", "safe-name"), "safe-file-id", "movies")
        ]

        response = self.test_app.post("/server/command/queue/safe-name?path_pair_id=movies", expect_errors=True)

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

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

    def test_bulk_queue_accepts_path_pair_identity_objects(self):
        seen_commands = []

        def side_effect(cmd: Controller.Command):
            seen_commands.append(cmd)
            cmd.callbacks[0].on_success()

        self.controller.queue_command = MagicMock()
        self.controller.queue_command.side_effect = side_effect
        self.controller.get_model_files.return_value = [
            self.__model_file("dup", "[\"movies\",\"dup\"]", "movies")
        ]

        response = self.test_app.post_json("/server/command/bulk/queue", {
            "files": [
                {"name": "dup", "path_pair_id": "movies"}
            ]
        })

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(seen_commands))
        self.assertEqual("[\"movies\",\"dup\"]", seen_commands[0].filename)

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

    def test_bulk_queue_rejects_control_characters_in_identity_resolved_file_id(self):
        self.controller.queue_command = MagicMock()

        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"files": [{"name": "safe-name", "file_id": "bad\x01id"}]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_bulk_queue_rejects_control_characters_in_raw_path_pair_id(self):
        self.controller.queue_command = MagicMock()

        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"files": [{"name": "safe-name", "path_pair_id": "bad\x01id"}]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_bulk_queue_rejects_control_characters_in_path_pair_id_resolved_file_id(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file("safe-name", "bad\x01id", "movies")
        ]

        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"files": [{"name": "safe-name", "path_pair_id": "movies"}]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

    def test_bulk_queue_rejects_control_characters_in_path_pair_id_resolved_model_name(self):
        self.controller.queue_command = MagicMock()
        self.controller.get_model_files.return_value = [
            self.__model_file(_PathPairIdentityMatchStr("bad\x01name", "safe-name"), "safe-file-id", "movies")
        ]

        response = self.test_app.post_json(
            "/server/command/bulk/queue",
            {"files": [{"name": "safe-name", "path_pair_id": "movies"}]},
            expect_errors=True
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid file path", response.text)
        self.controller.queue_command.assert_not_called()

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

        with patch.object(ControllerHandler, "_QUEUE_ACTION_TIMEOUT", 0.01):
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

        with patch.object(ControllerHandler, "_QUEUE_ACTION_TIMEOUT", 0.01):
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

        with patch.object(ControllerHandler, "_QUEUE_ACTION_TIMEOUT", 0.01):
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

    def test_queue_wait_trace_forwards_completed_outcomes(self):
        success_flow = "fractional-queue:0123456789abcdef"

        def succeed(command: Controller.Command):
            command.queue_trace_flow_id = success_flow
            command.callbacks[0].queue_trace_flow_id = success_flow
            command.callbacks[0].on_success()

        self.controller.queue_command = MagicMock(side_effect=succeed)
        self.assertEqual(200, self.test_app.post("/server/command/queue/test1").status_code)
        self.controller.record_queue_http_wait_trace.assert_called_once_with(
            "test1", True, True, flow_id=success_flow,
        )

        failure_flow = "fractional-queue:fedcba9876543210"

        def fail(command: Controller.Command):
            command.queue_trace_flow_id = failure_flow
            command.callbacks[0].queue_trace_flow_id = failure_flow
            command.callbacks[0].on_failure("missing", 404)

        self.controller.record_queue_http_wait_trace.reset_mock()
        self.controller.queue_command = MagicMock(side_effect=fail)
        self.assertEqual(
            404,
            self.test_app.post("/server/command/queue/test2", expect_errors=True).status_code,
        )
        self.controller.record_queue_http_wait_trace.assert_called_once_with(
            "test2", True, False, flow_id=failure_flow,
        )
        self.assertRegex(success_flow, r"^fractional-queue:[0-9a-f]{16}$")
        self.assertRegex(failure_flow, r"^fractional-queue:[0-9a-f]{16}$")

    def test_queue_times_out_when_callback_never_completes(self):
        self.controller.queue_command = MagicMock()

        with patch.object(ControllerHandler, "_QUEUE_ACTION_TIMEOUT", 0.01):
            response = self.test_app.post("/server/command/queue/test1", expect_errors=True)

        self.assertEqual(504, response.status_code)
        self.assertEqual("Operation timed out", response.text)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("test1", command.filename)
        self.controller.record_queue_http_wait_trace.assert_called_once_with(
            "test1", False, None, flow_id=None,
        )

    def test_queue_controller_failure_is_distinct_from_http_timeout(self):
        def fail(command: Controller.Command):
            command.callbacks[0].on_failure("initial authority expired", 409)

        self.controller.queue_command = MagicMock(side_effect=fail)

        response = self.test_app.post("/server/command/queue/test1", expect_errors=True)

        self.assertEqual(409, response.status_code)
        self.assertEqual("initial authority expired", response.text)
        self.controller.record_queue_http_wait_trace.assert_called_once_with(
            "test1", True, False, flow_id=None,
        )

    def test_pending_queue_http_wait_does_not_block_unrelated_summary_get(self):
        request_started = Event()
        request_finished = Event()
        responses = {}

        def hold(command: Controller.Command):
            request_started.set()

        self.controller.queue_command = MagicMock(side_effect=hold)
        self.controller.get_model_summary.return_value = {"model_version": 1}

        def issue_queue_request():
            try:
                responses["queue"] = self.test_app.post(
                    "/server/command/queue/test1", expect_errors=True,
                )
            finally:
                request_finished.set()

        queue_thread = Thread(target=issue_queue_request)
        with patch.object(ControllerHandler, "_QUEUE_ACTION_TIMEOUT", 0.2):
            queue_thread.start()
            self.assertTrue(request_started.wait(timeout=1.0))
            summary_response = self.test_app.get("/server/model/v1/summary")
            queue_thread.join(timeout=1.0)

        self.assertTrue(request_finished.is_set())
        self.assertEqual(200, summary_response.status_code)
        self.assertEqual({"model_version": 1}, summary_response.json)
        self.assertEqual(504, responses["queue"].status_code)

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
