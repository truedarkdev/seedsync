# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
from threading import BoundedSemaphore, Event
from typing import Optional, TypeGuard
from urllib.parse import unquote

import bottle
from bottle import HTTPResponse

from common import overrides
from controller.controller import Controller
from ..web_app import IHandler, WebApp


def _is_string_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


class WebResponseActionCallback(Controller.Command.ICallback):
    """
    Controller action callback used by model streams to wait for action
    status.
    Clients should call wait() method to wait for the status,
    then query the status from 'success' and 'error'
    """

    def __init__(self) -> None:
        self.__event = Event()
        self.success: bool | None = None
        self.error: str | None = None
        self.error_code = 400

    @overrides(Controller.Command.ICallback)
    def on_failure(self, error: str, error_code: int = 400) -> None:
        self.success = False
        self.error = error
        self.error_code = error_code
        self.__event.set()

    @overrides(Controller.Command.ICallback)
    def on_success(self) -> None:
        self.success = True
        self.__event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.__event.wait(timeout=timeout)


class ControllerHandler(IHandler):
    _ACTION_TIMEOUT = 30.0
    _MAX_BULK_ITEMS = 100
    _MAX_CONCURRENT_BULK_REQUESTS = 1
    _GUARDED_ACTIONS = {
        Controller.Command.Action.EXTRACT,
        Controller.Command.Action.DELETE_LOCAL,
        Controller.Command.Action.DELETE_REMOTE
    }

    def __init__(self, controller: Controller, local_path: str | None = None) -> None:
        self.__controller = controller
        self.__local_path_root = self.__normalize_local_path(local_path)
        self.__bulk_request_limiter = BoundedSemaphore(self._MAX_CONCURRENT_BULK_REQUESTS)

    @staticmethod
    def __normalize_local_path(local_path: str | None) -> Optional[str]:
        if local_path is None or local_path.strip() == "":
            return None
        return os.path.realpath(local_path)

    @staticmethod
    def __check_file_name_safe(file_name: str) -> Optional[HTTPResponse]:
        if (
            file_name == "" or
            any(ord(char) < 32 or ord(char) == 127 for char in file_name)
        ):
            return HTTPResponse(body="Invalid file path", status=400)
        return None

    def __check_path_safe(self, file_name: str) -> Optional[HTTPResponse]:
        if self.__local_path_root is None:
            return HTTPResponse(body="Invalid file path", status=400)

        guard_response = ControllerHandler.__check_file_name_safe(file_name)
        if guard_response:
            return guard_response

        candidate_path = os.path.realpath(os.path.join(self.__local_path_root, file_name))
        try:
            common_path = os.path.commonpath([self.__local_path_root, candidate_path])
        except ValueError:
            common_path = ""

        if (
            os.path.normcase(common_path) != os.path.normcase(self.__local_path_root) or
            os.path.normcase(candidate_path) == os.path.normcase(self.__local_path_root)
        ):
            return HTTPResponse(body="Invalid file path", status=400)
        return None

    @staticmethod
    def __query_param(name: str) -> str | None:
        return bottle.request.query.get(name)

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_post_handler(
            "/server/command/queue/<file_name>",
            self.__handle_action_queue,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/command/stop/<file_name>",
            self.__handle_action_stop,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/command/extract/<file_name>",
            self.__handle_action_extract,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/command/validate/<file_name>",
            self.__handle_action_validate,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/command/retry_move/<file_name>",
            self.__handle_action_retry_move,
            required_scope="write"
        )
        web_app.add_delete_handler(
            "/server/command/delete_local/<file_name>",
            self.__handle_action_delete_local,
            required_scope="write"
        )
        web_app.add_delete_handler(
            "/server/command/delete_remote/<file_name>",
            self.__handle_action_delete_remote,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/command/bulk/<action>",
            self.__handle_action_bulk,
            required_scope="write"
        )

    @staticmethod
    def __get_action(action: str) -> Controller.Command.Action | None:
        return {
            "queue": Controller.Command.Action.QUEUE,
            "stop": Controller.Command.Action.STOP,
            "extract": Controller.Command.Action.EXTRACT,
            "validate": Controller.Command.Action.VALIDATE,
            "retry_move": Controller.Command.Action.RETRY_MOVE,
            "delete_local": Controller.Command.Action.DELETE_LOCAL,
            "delete_remote": Controller.Command.Action.DELETE_REMOTE
        }.get(action)

    def __execute_action(
        self,
        action: Controller.Command.Action,
        file_name: str,
        timeout: float | None = None,
    ) -> tuple[WebResponseActionCallback, bool]:
        command = Controller.Command(action, file_name)
        callback = WebResponseActionCallback()
        command.add_callback(callback)
        self.__controller.queue_command(command)
        return callback, callback.wait(timeout=timeout)

    def __resolve_command_identifier(
        self,
        file_name: str,
        file_id: str | None = None,
        path_pair_id: str | None = None,
    ) -> tuple[str | None, str | None, HTTPResponse | None]:
        guard_response = self.__check_file_name_safe(file_name)
        if guard_response:
            return None, None, guard_response

        if file_id is not None:
            if file_id == "":
                return None, None, HTTPResponse(body="Invalid file_id query parameter", status=400)
            guard_response = self.__check_file_name_safe(file_id)
            if guard_response:
                return None, None, guard_response

        if path_pair_id is not None:
            if path_pair_id == "":
                return None, None, HTTPResponse(body="Invalid path_pair_id query parameter", status=400)
            guard_response = self.__check_file_name_safe(path_pair_id)
            if guard_response:
                return None, None, guard_response

        model_files = self.__controller.get_model_files()

        if file_id is not None:
            matches = [model_file for model_file in model_files if model_file.file_id == file_id]
            if len(matches) != 1:
                return None, None, HTTPResponse(body="File identity did not match exactly one file", status=400)
            guard_response = self.__check_file_name_safe(matches[0].name)
            if guard_response:
                return None, None, guard_response
            return file_id, matches[0].name, None

        if path_pair_id is not None:
            matches = [
                model_file for model_file in model_files
                if model_file.name == file_name and model_file.path_pair_id == path_pair_id
            ]
            if len(matches) != 1:
                return None, None, HTTPResponse(body="File identity did not match exactly one file", status=400)
            guard_response = self.__check_file_name_safe(matches[0].file_id)
            if guard_response:
                return None, None, guard_response
            guard_response = self.__check_file_name_safe(matches[0].name)
            if guard_response:
                return None, None, guard_response
            return matches[0].file_id, matches[0].name, None

        matches = [model_file for model_file in model_files if model_file.name == file_name]
        if len(matches) > 1:
            return None, None, HTTPResponse(
                body="File name '{}' is ambiguous; resend with file_id".format(file_name),
                status=400
            )
        if len(matches) == 1:
            return file_name, matches[0].name, None
        return file_name, file_name, None

    def __handle_action_queue(self, file_name: str) -> HTTPResponse:
        """
        Request a QUEUE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, _, error_response = self.__resolve_command_identifier(
            file_name,
            self.__query_param("file_id"),
            self.__query_param("path_pair_id")
        )
        if error_response:
            return error_response
        assert file_identifier is not None

        callback, completed = self.__execute_action(
            Controller.Command.Action.QUEUE,
            file_identifier,
            timeout=self._ACTION_TIMEOUT
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Queued file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_stop(self, file_name: str) -> HTTPResponse:
        """
        Request a STOP action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, _, error_response = self.__resolve_command_identifier(
            file_name,
            self.__query_param("file_id"),
            self.__query_param("path_pair_id")
        )
        if error_response:
            return error_response
        assert file_identifier is not None

        callback, completed = self.__execute_action(
            Controller.Command.Action.STOP,
            file_identifier,
            timeout=self._ACTION_TIMEOUT
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Stopped file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_retry_move(self, file_name: str) -> HTTPResponse:
        file_name = unquote(file_name)
        file_id = self.__query_param("file_id")
        if file_id is None:
            return HTTPResponse(body="file_id is required", status=400)
        file_identifier, _, error_response = self.__resolve_command_identifier(file_name, file_id)
        if error_response:
            return error_response
        assert file_identifier is not None
        callback, completed = self.__execute_action(
            Controller.Command.Action.RETRY_MOVE,
            file_identifier,
            timeout=self._ACTION_TIMEOUT,
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Move retry completed")
        return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_extract(self, file_name: str) -> HTTPResponse:
        """
        Request a EXTRACT action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, guard_name, error_response = self.__resolve_command_identifier(
            file_name,
            self.__query_param("file_id"),
            self.__query_param("path_pair_id")
        )
        if error_response:
            return error_response
        assert file_identifier is not None

        assert guard_name is not None
        guard_response = self.__check_path_safe(guard_name)
        if guard_response:
            return guard_response

        callback, completed = self.__execute_action(
            Controller.Command.Action.EXTRACT,
            file_identifier,
            timeout=self._ACTION_TIMEOUT
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Requested extraction for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_delete_local(self, file_name: str) -> HTTPResponse:
        """
        Request a DELETE LOCAL action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, guard_name, error_response = self.__resolve_command_identifier(
            file_name,
            self.__query_param("file_id"),
            self.__query_param("path_pair_id")
        )
        if error_response:
            return error_response
        assert file_identifier is not None

        assert guard_name is not None
        guard_response = self.__check_path_safe(guard_name)
        if guard_response:
            return guard_response

        callback, completed = self.__execute_action(
            Controller.Command.Action.DELETE_LOCAL,
            file_identifier,
            timeout=self._ACTION_TIMEOUT
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Requested local delete for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_validate(self, file_name: str) -> HTTPResponse:
        """
        Request a VALIDATE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, _, error_response = self.__resolve_command_identifier(
            file_name,
            self.__query_param("file_id"),
            self.__query_param("path_pair_id")
        )
        if error_response:
            return error_response
        assert file_identifier is not None

        callback, completed = self.__execute_action(
            Controller.Command.Action.VALIDATE,
            file_identifier,
            timeout=self._ACTION_TIMEOUT
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Requested validation for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_delete_remote(self, file_name: str) -> HTTPResponse:
        """
        Request a DELETE REMOTE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, guard_name, error_response = self.__resolve_command_identifier(
            file_name,
            self.__query_param("file_id"),
            self.__query_param("path_pair_id")
        )
        if error_response:
            return error_response
        assert file_identifier is not None

        assert guard_name is not None
        guard_response = self.__check_path_safe(guard_name)
        if guard_response:
            return guard_response

        callback, completed = self.__execute_action(
            Controller.Command.Action.DELETE_REMOTE,
            file_identifier,
            timeout=self._ACTION_TIMEOUT
        )
        if not completed:
            return HTTPResponse(body="Operation timed out", status=504)
        if callback.success:
            return HTTPResponse(body="Requested remote delete for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_bulk(self, action: str) -> HTTPResponse:
        command_action = self.__get_action(action)
        if command_action is None:
            return HTTPResponse(body="Unsupported bulk action '{}'".format(action), status=404)

        payload_value = bottle.request.json
        payload = payload_value if _is_string_object_dict(payload_value) else {}
        file_entries = payload.get("files")
        filenames = payload.get("filenames")

        ordered_commands: list[tuple[str, str, str | None]] = []
        seen_identifiers: set[str] = set()
        if _is_object_list(file_entries) and len(file_entries) > 0:
            if len(file_entries) > self._MAX_BULK_ITEMS:
                return HTTPResponse(
                    body="Bulk command exceeds maximum of {} items".format(self._MAX_BULK_ITEMS),
                    status=413
                )
            for file_entry in file_entries:
                if not _is_string_object_dict(file_entry):
                    return HTTPResponse(body="Bulk command files must be objects", status=400)
                file_name = file_entry.get("name")
                file_id = file_entry.get("file_id")
                path_pair_id = file_entry.get("path_pair_id")
                if not isinstance(file_name, str) or file_name == "":
                    return HTTPResponse(body="Bulk command file names must be non-empty strings", status=400)
                if file_id is not None and (not isinstance(file_id, str) or file_id == ""):
                    return HTTPResponse(body="Bulk command file_id values must be non-empty strings", status=400)
                if path_pair_id is not None and (not isinstance(path_pair_id, str) or path_pair_id == ""):
                    return HTTPResponse(body="Bulk command path_pair_id values must be non-empty strings", status=400)

                identifier, guard_name, error_response = self.__resolve_command_identifier(
                    file_name,
                    file_id,
                    path_pair_id
                )
                if error_response:
                    return error_response
                assert identifier is not None
                if identifier not in seen_identifiers:
                    ordered_commands.append((file_name, identifier, guard_name))
                    seen_identifiers.add(identifier)
        elif _is_object_list(filenames) and len(filenames) > 0:
            if len(filenames) > self._MAX_BULK_ITEMS:
                return HTTPResponse(
                    body="Bulk command exceeds maximum of {} items".format(self._MAX_BULK_ITEMS),
                    status=413
                )
            for file_name in filenames:
                if not isinstance(file_name, str) or file_name == "":
                    return HTTPResponse(body="Bulk command filenames must be non-empty strings", status=400)
                identifier, guard_name, error_response = self.__resolve_command_identifier(file_name)
                if error_response:
                    return error_response
                assert identifier is not None
                if identifier not in seen_identifiers:
                    ordered_commands.append((file_name, identifier, guard_name))
                    seen_identifiers.add(identifier)
        else:
            return HTTPResponse(
                body="Bulk command requires a non-empty 'files' or 'filenames' list",
                status=400
            )

        if not self.__bulk_request_limiter.acquire(blocking=False):
            return HTTPResponse(body="Bulk request already in progress", status=429)

        try:
            failures: list[str] = []
            success_count = 0
            for display_name, identifier, guard_name in ordered_commands:
                if command_action in self._GUARDED_ACTIONS:
                    if guard_name is None:
                        failures.append("'{}': Invalid file path".format(display_name))
                        continue
                    guard_response = self.__check_path_safe(guard_name)
                    if guard_response:
                        failures.append("'{}': Invalid file path".format(display_name))
                        continue

                callback, completed = self.__execute_action(
                    command_action,
                    identifier,
                    timeout=self._ACTION_TIMEOUT
                )
                if not completed:
                    failures.append("'{}': Operation timed out".format(display_name))
                elif callback.success:
                    success_count += 1
                else:
                    failures.append("'{}': {}".format(display_name, callback.error))

            body = "Bulk {} completed: {} succeeded, {} failed".format(
                action.replace("_", " "),
                success_count,
                len(failures)
            )
            if failures:
                body += ". " + "; ".join(failures)
                return HTTPResponse(body=body, status=400)
            return HTTPResponse(body=body)
        finally:
            self.__bulk_request_limiter.release()
