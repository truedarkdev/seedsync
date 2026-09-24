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
        payload = self.__context.performance_diagnostics.snapshot(since_sequence, limit)
        payload["runtime_counts"] = self.__controller.get_performance_diagnostic_runtime_counts()
        breadcrumb_trace = getattr(self.__context, "breadcrumb_trace", None)
        health_snapshot = getattr(breadcrumb_trace, "performance_diagnostic_health_snapshot", None)
        if callable(health_snapshot):
            try:
                payload["trace_health"] = health_snapshot()
            except Exception:
                payload["trace_health"] = self.__unknown_trace_health("snapshot_failed")
        else:
            payload["trace_health"] = self.__unknown_trace_health("collector_unavailable")
        return self.__json_response(payload)

    @staticmethod
    def __unknown_trace_health(reason: str) -> dict[str, object]:
        fields = {
            "ingress": ("pending_known", "critical_pending", "normal_pending", "critical_rejected",
                        "normal_rejected", "unresolved"),
            "durable": ("accepted", "written", "lost", "critical_lost", "normal_lost", "unknown"),
        }
        return {
            section: {
                "known": False,
                **{field: False if field == "pending_known" else None for field in section_fields},
                "sample_started_at_utc": None, "sampled_at_utc": None, "reason": reason,
            }
            for section, section_fields in fields.items()
        }

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
