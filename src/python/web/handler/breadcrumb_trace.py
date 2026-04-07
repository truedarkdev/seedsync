# Copyright 2026, SeedSync Contributors, All rights reserved.

import json

import bottle
from bottle import HTTPResponse

from common import Context, overrides
from ..web_app import IHandler, WebApp


class BreadcrumbTraceHandler(IHandler):
    def __init__(self, context: Context):
        self.__context = context

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
        web_app.add_handler(
            "/server/breadcrumbs/get",
            self.__handle_get_breadcrumbs,
            required_scope="admin"
        )

    def __handle_get_breadcrumbs(self):
        breadcrumb_trace = self.__context.breadcrumb_trace
        since_version = bottle.request.query.get("since_version")
        if since_version is not None and since_version != "":
            try:
                since_version = int(since_version)
            except ValueError:
                return HTTPResponse(body="since_version must be an integer", status=400)
        else:
            since_version = None
        payload = {
            **breadcrumb_trace.snapshot(since_version=since_version),
        }
        return HTTPResponse(body=json.dumps(payload))
