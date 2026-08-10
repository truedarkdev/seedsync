# Copyright 2026, SeedSync Contributors, All rights reserved.

import json

import bottle
from bottle import HTTPResponse

from common import Context, overrides
from controller import Controller
from ..web_app import IHandler, WebApp


class PerformanceDiagnosticsHandler(IHandler):
    """Admin-only bounded local resource diagnostics API."""

    _PATH = "/server/admin/performance-diagnostics/v1"
    _JSON_HEADERS = {"Content-Type": "application/json", "X-Content-Type-Options": "nosniff"}

    def __init__(self, context: Context, controller: Controller) -> None:
        self.__context = context
        self.__controller = controller

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_handler(self._PATH, self.__get, required_scope="admin")
        web_app.add_handler(self._PATH + "/ownership", self.__ownership, required_scope="admin")
        web_app.add_post_handler(self._PATH + "/reset", self.__reset, required_scope="admin")
        web_app.add_handler(self._PATH + "/export", self.__export, required_scope="admin")

    @staticmethod
    def __optional_int(name: str) -> int | HTTPResponse | None:
        value = getattr(bottle.request.query, "get")(name)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except ValueError:
            return HTTPResponse(body="{} must be an integer".format(name), status=400)

    def __get(self) -> HTTPResponse:
        since_sequence = self.__optional_int("since_sequence")
        if isinstance(since_sequence, HTTPResponse):
            return since_sequence
        if since_sequence is not None and since_sequence < 0:
            return HTTPResponse(body="since_sequence must be zero or greater", status=400)
        limit = self.__optional_int("limit")
        if isinstance(limit, HTTPResponse):
            return limit
        if limit is not None and (limit < 1 or limit > self.__context.performance_diagnostics.retention_depth):
            return HTTPResponse(body="limit is outside the retained sample bounds", status=400)
        return self.__json_response(self.__context.performance_diagnostics.snapshot(since_sequence, limit))

    def __reset(self) -> HTTPResponse:
        self.__context.performance_diagnostics.reset()
        return self.__json_response({"status": "reset"})

    def __ownership(self) -> HTTPResponse:
        return self.__json_response(self.__controller.get_memory_ownership_census())

    def __export(self) -> HTTPResponse:
        # Export has no caller-supplied selectors and therefore cannot cause
        # filesystem access or unbounded support payloads.
        response = self.__context.performance_diagnostics.export_snapshot()
        return self.__json_response(response)

    @classmethod
    def __json_response(cls, payload: object) -> HTTPResponse:
        return HTTPResponse(body=json.dumps(payload, separators=(",", ":"), allow_nan=False), headers=cls._JSON_HEADERS)
