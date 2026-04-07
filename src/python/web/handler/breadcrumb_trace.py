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
        web_app.add_post_handler(
            "/server/breadcrumbs/reset",
            self.__handle_reset_breadcrumbs,
            required_scope="admin"
        )

    def __handle_get_breadcrumbs(self):
        breadcrumb_trace = self.__context.breadcrumb_trace
        since_version = self.__parse_optional_int("since_version")
        if isinstance(since_version, HTTPResponse):
            return since_version
        limit = self.__parse_optional_int("limit")
        if isinstance(limit, HTTPResponse):
            return limit
        if limit is not None and limit < 1:
            return HTTPResponse(body="limit must be greater than 0", status=400)

        order = self.__parse_optional_string("order")
        if order is not None:
            order = order.strip().lower()
            if order not in {"asc", "desc"}:
                return HTTPResponse(body="order must be 'asc' or 'desc'", status=400)

        payload = {
            **breadcrumb_trace.snapshot(
                since_version=since_version,
                limit=limit,
                corr_id=self.__parse_optional_string("corr_id"),
                flow_id=self.__parse_optional_string("flow_id"),
                stage=self.__parse_optional_string("stage"),
                event_type=self.__parse_optional_string("event_type"),
                path_pair_id=self.__parse_optional_string("path_pair_id"),
                file_id=self.__parse_optional_string("file_id"),
                order=order if order is not None else "asc",
            ),
        }
        return HTTPResponse(body=json.dumps(payload))

    def __handle_reset_breadcrumbs(self):
        self.__context.breadcrumb_trace.reset()
        return HTTPResponse(body=json.dumps({"status": "reset"}))

    @staticmethod
    def __parse_optional_string(name: str):
        value = bottle.request.query.get(name)
        if value is None or value == "":
            return None
        return value

    @staticmethod
    def __parse_optional_int(name: str):
        value = bottle.request.query.get(name)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except ValueError:
            return HTTPResponse(body="{} must be an integer".format(name), status=400)
