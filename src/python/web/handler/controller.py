# Copyright 2017, Inderpreet Singh, All rights reserved.

from threading import Event
from urllib.parse import unquote

import bottle
from bottle import HTTPResponse

from common import overrides
from controller import Controller
from ..web_app import IHandler, WebApp


class WebResponseActionCallback(Controller.Command.ICallback):
    """
    Controller action callback used by model streams to wait for action
    status.
    Clients should call wait() method to wait for the status,
    then query the status from 'success' and 'error'
    """

    def __init__(self):
        self.__event = Event()
        self.success = None
        self.error = None
        self.error_code = 400

    @overrides(Controller.Command.ICallback)
    def on_failure(self, error: str, error_code: int = 400):
        self.success = False
        self.error = error
        self.error_code = error_code
        self.__event.set()

    @overrides(Controller.Command.ICallback)
    def on_success(self):
        self.success = True
        self.__event.set()

    def wait(self):
        self.__event.wait()


class ControllerHandler(IHandler):
    def __init__(self, controller: Controller):
        self.__controller = controller

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
        web_app.add_post_handler("/server/command/queue/<file_name>", self.__handle_action_queue)
        web_app.add_post_handler("/server/command/stop/<file_name>", self.__handle_action_stop)
        web_app.add_post_handler("/server/command/extract/<file_name>", self.__handle_action_extract)
        web_app.add_delete_handler("/server/command/delete_local/<file_name>", self.__handle_action_delete_local)
        web_app.add_delete_handler("/server/command/delete_remote/<file_name>", self.__handle_action_delete_remote)
        web_app.add_post_handler("/server/command/bulk/<action>", self.__handle_action_bulk)

    @staticmethod
    def __get_action(action: str):
        return {
            "queue": Controller.Command.Action.QUEUE,
            "stop": Controller.Command.Action.STOP,
            "extract": Controller.Command.Action.EXTRACT,
            "delete_local": Controller.Command.Action.DELETE_LOCAL,
            "delete_remote": Controller.Command.Action.DELETE_REMOTE
        }.get(action)

    def __execute_action(self, action, file_name: str):
        command = Controller.Command(action, file_name)
        callback = WebResponseActionCallback()
        command.add_callback(callback)
        self.__controller.queue_command(command)
        callback.wait()
        return callback

    def __resolve_command_identifier(self, file_name: str, file_id: str = None, path_pair_id: str = None):
        model_files = self.__controller.get_model_files()

        if file_id is not None:
            if not isinstance(file_id, str) or file_id == "":
                return None, HTTPResponse(body="Invalid file_id query parameter", status=400)
            matches = [model_file for model_file in model_files if model_file.file_id == file_id]
            if len(matches) != 1:
                return None, HTTPResponse(body="File identity did not match exactly one file", status=400)
            return file_id, None

        if path_pair_id is not None:
            if not isinstance(path_pair_id, str) or path_pair_id == "":
                return None, HTTPResponse(body="Invalid path_pair_id query parameter", status=400)
            matches = [
                model_file for model_file in model_files
                if model_file.name == file_name and model_file.path_pair_id == path_pair_id
            ]
            if len(matches) != 1:
                return None, HTTPResponse(body="File identity did not match exactly one file", status=400)
            return matches[0].file_id, None

        matches = [model_file for model_file in model_files if model_file.name == file_name]
        if len(matches) > 1:
            return None, HTTPResponse(
                body="File name '{}' is ambiguous; resend with file_id".format(file_name),
                status=400
            )
        return file_name, None

    def __handle_action_queue(self, file_name: str):
        """
        Request a QUEUE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, error_response = self.__resolve_command_identifier(
            file_name,
            bottle.request.query.get("file_id"),
            bottle.request.query.get("path_pair_id")
        )
        if error_response:
            return error_response

        callback = self.__execute_action(Controller.Command.Action.QUEUE, file_identifier)
        if callback.success:
            return HTTPResponse(body="Queued file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_stop(self, file_name: str):
        """
        Request a STOP action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, error_response = self.__resolve_command_identifier(
            file_name,
            bottle.request.query.get("file_id"),
            bottle.request.query.get("path_pair_id")
        )
        if error_response:
            return error_response

        callback = self.__execute_action(Controller.Command.Action.STOP, file_identifier)
        if callback.success:
            return HTTPResponse(body="Stopped file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_extract(self, file_name: str):
        """
        Request a EXTRACT action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, error_response = self.__resolve_command_identifier(
            file_name,
            bottle.request.query.get("file_id"),
            bottle.request.query.get("path_pair_id")
        )
        if error_response:
            return error_response

        callback = self.__execute_action(Controller.Command.Action.EXTRACT, file_identifier)
        if callback.success:
            return HTTPResponse(body="Requested extraction for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_delete_local(self, file_name: str):
        """
        Request a DELETE LOCAL action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, error_response = self.__resolve_command_identifier(
            file_name,
            bottle.request.query.get("file_id"),
            bottle.request.query.get("path_pair_id")
        )
        if error_response:
            return error_response

        callback = self.__execute_action(Controller.Command.Action.DELETE_LOCAL, file_identifier)
        if callback.success:
            return HTTPResponse(body="Requested local delete for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_delete_remote(self, file_name: str):
        """
        Request a DELETE REMOTE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        file_identifier, error_response = self.__resolve_command_identifier(
            file_name,
            bottle.request.query.get("file_id"),
            bottle.request.query.get("path_pair_id")
        )
        if error_response:
            return error_response

        callback = self.__execute_action(Controller.Command.Action.DELETE_REMOTE, file_identifier)
        if callback.success:
            return HTTPResponse(body="Requested remote delete for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=callback.error_code)

    def __handle_action_bulk(self, action: str):
        command_action = self.__get_action(action)
        if command_action is None:
            return HTTPResponse(body="Unsupported bulk action '{}'".format(action), status=404)

        payload = bottle.request.json or {}
        file_entries = payload.get("files")
        filenames = payload.get("filenames")

        ordered_commands = []
        seen_identifiers = set()
        if isinstance(file_entries, list) and len(file_entries) > 0:
            for file_entry in file_entries:
                if not isinstance(file_entry, dict):
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

                identifier, error_response = self.__resolve_command_identifier(file_name, file_id, path_pair_id)
                if error_response:
                    return error_response
                if identifier not in seen_identifiers:
                    ordered_commands.append((file_name, identifier))
                    seen_identifiers.add(identifier)
        elif isinstance(filenames, list) and len(filenames) > 0:
            for file_name in filenames:
                if not isinstance(file_name, str) or file_name == "":
                    return HTTPResponse(body="Bulk command filenames must be non-empty strings", status=400)
                identifier, error_response = self.__resolve_command_identifier(file_name)
                if error_response:
                    return error_response
                if identifier not in seen_identifiers:
                    ordered_commands.append((file_name, identifier))
                    seen_identifiers.add(identifier)
        else:
            return HTTPResponse(
                body="Bulk command requires a non-empty 'files' or 'filenames' list",
                status=400
            )

        failures = []
        success_count = 0
        for display_name, identifier in ordered_commands:
            callback = self.__execute_action(command_action, identifier)
            if callback.success:
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
