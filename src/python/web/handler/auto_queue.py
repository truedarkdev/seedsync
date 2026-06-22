# Copyright 2017, Inderpreet Singh, All rights reserved.

from bottle import HTTPResponse
from urllib.parse import unquote

from common import overrides
from controller import AutoQueuePersist, AutoQueuePattern
from ..web_app import IHandler, WebApp
from ..serialize import SerializeAutoQueue


class AutoQueueHandler(IHandler):
    def __init__(self, auto_queue_persist: AutoQueuePersist):
        self.__auto_queue_persist = auto_queue_persist

    @staticmethod
    def __plain_text_response(body: str, status: int = 200):
        return HTTPResponse(
            body=body,
            status=status,
            headers={
                "Content-Type": "text/plain",
                "X-Content-Type-Options": "nosniff"
            }
        )

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
        web_app.add_handler(
            "/server/autoqueue/get",
            self.__handle_get_autoqueue,
            required_scope="read"
        )
        web_app.add_post_handler(
            "/server/autoqueue/add/<pattern>",
            self.__handle_add_autoqueue,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/autoqueue/remove/<pattern>",
            self.__handle_remove_autoqueue,
            required_scope="write"
        )

    def __handle_get_autoqueue(self):
        patterns = list(self.__auto_queue_persist.patterns)
        patterns.sort(key=lambda p: p.pattern)
        out_json = SerializeAutoQueue.patterns(patterns)
        return HTTPResponse(body=out_json)

    def __handle_add_autoqueue(self, pattern: str):
        # value is double encoded
        pattern = unquote(pattern)

        aqp = AutoQueuePattern(pattern=pattern)

        if aqp in self.__auto_queue_persist.patterns:
            return self.__plain_text_response("Auto-queue pattern '{}' already exists.".format(pattern), status=409)
        else:
            try:
                self.__auto_queue_persist.add_pattern(aqp)
                return self.__plain_text_response("Added auto-queue pattern '{}'.".format(pattern))
            except ValueError as e:
                return self.__plain_text_response(str(e), status=400)

    def __handle_remove_autoqueue(self, pattern: str):
        # value is double encoded
        pattern = unquote(pattern)

        aqp = AutoQueuePattern(pattern=pattern)

        if aqp not in self.__auto_queue_persist.patterns:
            return self.__plain_text_response("Auto-queue pattern '{}' doesn't exist.".format(pattern), status=404)
        else:
            self.__auto_queue_persist.remove_pattern(aqp)
            return self.__plain_text_response("Removed auto-queue pattern '{}'.".format(pattern))
