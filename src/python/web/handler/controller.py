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

    @overrides(Controller.Command.ICallback)
    def on_failure(self, error: str):
        self.success = False
        self.error = error
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

    def __handle_action_queue(self, file_name: str):
        """
        Request a QUEUE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        callback = self.__execute_action(Controller.Command.Action.QUEUE, file_name)
        if callback.success:
            return HTTPResponse(body="Queued file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=400)

    def __handle_action_stop(self, file_name: str):
        """
        Request a STOP action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        callback = self.__execute_action(Controller.Command.Action.STOP, file_name)
        if callback.success:
            return HTTPResponse(body="Stopped file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=400)

    def __handle_action_extract(self, file_name: str):
        """
        Request a EXTRACT action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        callback = self.__execute_action(Controller.Command.Action.EXTRACT, file_name)
        if callback.success:
            return HTTPResponse(body="Requested extraction for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=400)

    def __handle_action_delete_local(self, file_name: str):
        """
        Request a DELETE LOCAL action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        callback = self.__execute_action(Controller.Command.Action.DELETE_LOCAL, file_name)
        if callback.success:
            return HTTPResponse(body="Requested local delete for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=400)

    def __handle_action_delete_remote(self, file_name: str):
        """
        Request a DELETE REMOTE action
        :param file_name:
        :return:
        """
        # value is double encoded
        file_name = unquote(file_name)

        callback = self.__execute_action(Controller.Command.Action.DELETE_REMOTE, file_name)
        if callback.success:
            return HTTPResponse(body="Requested remote delete for file '{}'".format(file_name))
        else:
            return HTTPResponse(body=callback.error, status=400)

    def __handle_action_bulk(self, action: str):
        command_action = self.__get_action(action)
        if command_action is None:
            return HTTPResponse(body="Unsupported bulk action '{}'".format(action), status=404)

        payload = bottle.request.json or {}
        file_names = payload.get("filenames")
        if not isinstance(file_names, list) or len(file_names) == 0:
            return HTTPResponse(body="Bulk command requires a non-empty 'filenames' list", status=400)

        ordered_names = []
        seen_names = set()
        for file_name in file_names:
            if not isinstance(file_name, str) or file_name == "":
                return HTTPResponse(body="Bulk command filenames must be non-empty strings", status=400)
            if file_name not in seen_names:
                ordered_names.append(file_name)
                seen_names.add(file_name)

        failures = []
        success_count = 0
        for file_name in ordered_names:
            callback = self.__execute_action(command_action, file_name)
            if callback.success:
                success_count += 1
            else:
                failures.append("'{}': {}".format(file_name, callback.error))

        body = "Bulk {} completed: {} succeeded, {} failed".format(
            action.replace("_", " "),
            success_count,
            len(failures)
        )
        if failures:
            body += ". " + "; ".join(failures)
            return HTTPResponse(body=body, status=400)
        return HTTPResponse(body=body)
