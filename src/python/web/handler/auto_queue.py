# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
from bottle import HTTPResponse
from urllib.parse import unquote
from threading import Lock

from common import overrides
from controller import AutoQueuePersist, AutoQueuePattern
from ..web_app import IHandler, WebApp
from ..serialize import SerializeAutoQueue

logger = logging.getLogger(__name__)


class AutoQueueHandler(IHandler):
    __NOSNIFF = "nosniff"

    def __init__(self, auto_queue_persist: AutoQueuePersist):
        self.__auto_queue_persist = auto_queue_persist
        self.__write_lock = Lock()

    @staticmethod
    def __plain_text_response(body: str, status: int = 200):
        return HTTPResponse(
            body=body,
            status=status,
            headers={
                "Content-Type": "text/plain",
                "X-Content-Type-Options": AutoQueueHandler.__NOSNIFF
            }
        )

    @staticmethod
    def __json_response(body: str, status: int = 200):
        return HTTPResponse(
            body=body,
            status=status,
            content_type="application/json",
            headers={
                "X-Content-Type-Options": AutoQueueHandler.__NOSNIFF
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
        return self.__json_response(out_json)

    def __handle_add_autoqueue(self, pattern: str):
        # value is double encoded
        pattern = unquote(pattern)

        aqp = AutoQueuePattern(pattern=pattern)

        with self.__write_lock:
            if aqp in self.__auto_queue_persist.patterns:
                return self.__plain_text_response("Auto-queue pattern '{}' already exists.".format(pattern), status=409)
            try:
                self.__auto_queue_persist.add_pattern(aqp)
            except ValueError as e:
                return self.__plain_text_response(str(e), status=400)
            except Exception:
                try:
                    self.__auto_queue_persist.remove_pattern(aqp)
                except Exception:
                    logger.exception("Failed to roll back auto-queue after adding pattern %r", pattern)
                logger.exception("Failed to persist auto-queue after adding pattern %r", pattern)
                return self.__plain_text_response("Failed to persist auto-queue", status=500)
            return self.__plain_text_response("Added auto-queue pattern '{}'.".format(pattern))

    def __handle_remove_autoqueue(self, pattern: str):
        # value is double encoded
        pattern = unquote(pattern)

        aqp = AutoQueuePattern(pattern=pattern)

        with self.__write_lock:
            if aqp not in self.__auto_queue_persist.patterns:
                return self.__plain_text_response("Auto-queue pattern '{}' doesn't exist.".format(pattern), status=404)
            try:
                self.__auto_queue_persist.remove_pattern(aqp)
            except Exception:
                try:
                    self.__auto_queue_persist.add_pattern(aqp)
                except Exception:
                    logger.exception("Failed to roll back auto-queue after removing pattern %r", pattern)
                logger.exception("Failed to persist auto-queue after removing pattern %r", pattern)
                return self.__plain_text_response("Failed to persist auto-queue", status=500)
            return self.__plain_text_response("Removed auto-queue pattern '{}'.".format(pattern))
