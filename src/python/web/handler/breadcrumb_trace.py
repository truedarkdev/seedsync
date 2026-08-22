# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Admin HTTP access to the bounded breadcrumb trace."""

from __future__ import annotations

import copy
import inspect
import json
import time
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

import bottle
from bottle import HTTPResponse

from common import Context, overrides
from ..web_app import IHandler, WebApp


class BreadcrumbTraceHandler(IHandler):
    """Legacy snapshot plus bounded, versioned breadcrumb diagnostics API."""

    _BASE_PATH = "/server/breadcrumbs"
    _V1_PATH = _BASE_PATH + "/v1"
    _JSON_HEADERS = {
        "Content-Type": "application/json",
        "X-Content-Type-Options": "nosniff",
    }

    # API limits are deliberately independent from collector retention.
    _DEFAULT_PAGE_LIMIT = 100
    _MAX_PAGE_LIMIT = 256
    _DEFAULT_EXPORT_LIMIT = 1024
    _MAX_EXPORT_LIMIT = 2048
    _MAX_REQUEST_BYTES = 64 * 1024
    _MAX_RESPONSE_BYTES = 2 * 1024 * 1024
    _MAX_FILTER_STRING_LENGTH = 256
    _MAX_STREAM_INTERVAL_SECONDS = 60.0
    _DEFAULT_STREAM_INTERVAL_SECONDS = 1.0
    _DEFAULT_HEARTBEAT_SECONDS = 15.0

    _QUERY_ALIASES = {
        "corr_id": "correlation_id",
        "correlation": "correlation_id",
        "flow": "flow_id",
        "created_after_ms": "start_time_ms",
        "created_before_ms": "end_time_ms",
        "min_time_ms": "start_time_ms",
        "max_time_ms": "end_time_ms",
        "min_version": "since_version",
        "max_version": "until_version",
    }
    _QUERY_FIELDS = frozenset({
        "limit", "since_version", "until_version", "version", "order",
        "category", "category_prefix", "level", "correlation_id", "corr_id",
        "correlation", "flow_id", "flow", "source", "stage", "event_type",
        "start_time_ms", "end_time_ms", "created_after_ms", "created_before_ms",
        "min_time_ms", "max_time_ms", "min_version", "max_version", "format",
        "follow", "interval_ms", "heartbeat_ms",
    })
    _CLEAR_SCOPE_FIELDS = frozenset({
        "corr_id", "correlation_id", "correlation", "flow_id", "flow", "stage", "event_type",
        "path_pair_id", "file_id", "source", "category", "level", "category_prefix",
    })

    def __init__(self, context: Context):
        self.__context = context

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
        self.__add_get(web_app, self._BASE_PATH + "/get", self.__handle_get_breadcrumbs)
        self.__add_post(web_app, self._BASE_PATH + "/reset", self.__handle_reset_breadcrumbs)

        for suffix, handler in (
            ("/capabilities", self.__handle_capabilities),
            ("/events", self.__handle_events),
            ("/stream", self.__handle_stream),
            ("/export", self.__handle_export),
            ("/policy", self.__handle_policy),
        ):
            self.__add_get(web_app, self._V1_PATH + suffix, handler)
        for suffix, handler in (
            ("/policy/validate", self.__handle_policy_validate),
            ("/policy/apply", self.__handle_policy_apply),
            ("/policy/reset", self.__handle_policy_reset),
            ("/clear", self.__handle_clear),
        ):
            self.__add_post(web_app, self._V1_PATH + suffix, handler)

    @classmethod
    def __add_get(cls, web_app: WebApp, path: str, handler: Callable[[], object]) -> None:
        web_app.add_handler(
            path, handler, required_scope="admin", always_auth=True,
        )

    @classmethod
    def __add_post(cls, web_app: WebApp, path: str, handler: Callable[[], object]) -> None:
        web_app.add_post_handler(
            path, handler, required_scope="admin", always_auth=True,
        )

    # ------------------------------------------------------------------
    # Legacy API

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
        limit = min(limit if limit is not None else self._DEFAULT_PAGE_LIMIT, self._MAX_PAGE_LIMIT)

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
        # The old endpoint historically exposed a count window.  The active
        # collector may now use a byte budget with no count cap; retain the
        # legacy JSON field for callers that still deserialize it as a number.
        if payload.get("max_entries") is None:
            payload["max_entries"] = 128
        return self.__bounded_json_payload(payload, "entries", self._MAX_PAGE_LIMIT)

    def __handle_reset_breadcrumbs(self):
        self.__context.breadcrumb_trace.reset()
        return self.__json_response({"status": "reset"})

    # ------------------------------------------------------------------
    # Versioned API

    def __handle_capabilities(self):
        return self.__service_response("capabilities")

    def __handle_policy(self):
        return self.__service_response("policy_snapshot")

    def __handle_events(self):
        filters = self.__read_query_filters(self._MAX_PAGE_LIMIT, self._DEFAULT_PAGE_LIMIT)
        if isinstance(filters, HTTPResponse):
            return filters
        result = self.__query_events(filters)
        return result if isinstance(result, HTTPResponse) else self.__bounded_json_payload(
            result, "events", self._MAX_PAGE_LIMIT
        )

    def __handle_export(self):
        filters = self.__read_query_filters(self._MAX_EXPORT_LIMIT, self._DEFAULT_EXPORT_LIMIT)
        if isinstance(filters, HTTPResponse):
            return filters
        output_format = filters.pop("format", "json")
        if output_format not in ("json", "jsonl"):
            return self.__error("format must be 'json' or 'jsonl'", 400)
        result = self.__call_service("export_events", filters)
        if isinstance(result, HTTPResponse) and self.__has_deep_service_filters(filters):
            # Older collectors forward export kwargs into snapshot(), which
            # does not know the richer v1 vocabulary. Retry through the
            # compatibility projection only for that mixed-version seam.
            result = self.__call_service("export_events", self.__service_query_filters(filters))
        if not isinstance(result, HTTPResponse):
            result = self.__apply_deep_filters(result, filters)
        if isinstance(result, HTTPResponse):
            return result
        bounded = self.__bounded_payload(result, "events", self._MAX_EXPORT_LIMIT)
        if output_format == "jsonl":
            events = self.__payload_events(bounded) or []
            header = {key: value for key, value in bounded.items() if key not in {"events", "entries", "latest_failure_entry"}}
            header["record_type"] = "breadcrumb-metadata"
            header["export_truncated"] = bool(header.get("export_truncated", False))
            header["export_event_count"] = len(events)
            header["entry_count"] = len(events)
            header["returned_event_count"] = len(events)
            lines = [json.dumps(header, separators=(",", ":"), allow_nan=False) + "\n"]
            size = len(lines[0].encode("utf-8"))
            if size > self._MAX_RESPONSE_BYTES:
                return self.__error("breadcrumb response exceeds the bounded response size", 413)
            for event in events:
                line = json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n"
                encoded = line.encode("utf-8")
                if size + len(encoded) > self._MAX_RESPONSE_BYTES:
                    break
                lines.append(line)
                size += len(encoded)
            if len(lines) - 1 < len(events):
                # Rewrite only the bounded header, not the collected records.
                header["export_truncated"] = True
                header["export_event_count"] = len(lines) - 1
                header["entry_count"] = len(lines) - 1
                header["returned_event_count"] = len(lines) - 1
                header_line = json.dumps(header, separators=(",", ":"), allow_nan=False) + "\n"
                lines[0] = header_line
            return HTTPResponse(body="".join(lines), status=200, headers={
                "Content-Type": "application/x-ndjson", "X-Content-Type-Options": "nosniff",
            })
        return self.__bounded_json_payload(bounded, "events", self._MAX_EXPORT_LIMIT)

    def __handle_stream(self):
        filters = self.__read_query_filters(self._MAX_PAGE_LIMIT, self._DEFAULT_PAGE_LIMIT)
        if isinstance(filters, HTTPResponse):
            return filters
        follow = filters.pop("follow", False)
        interval_ms = filters.pop("interval_ms", None)
        heartbeat_ms = filters.pop("heartbeat_ms", None)
        interval = interval_ms / 1000.0 if interval_ms is not None else self._DEFAULT_STREAM_INTERVAL_SECONDS
        heartbeat = heartbeat_ms / 1000.0 if heartbeat_ms is not None else self._DEFAULT_HEARTBEAT_SECONDS
        reconnect_id = bottle.request.get_header("Last-Event-ID", "").strip()
        if "since_version" not in filters and reconnect_id:
            try:
                reconnect_version = int(reconnect_id)
            except ValueError:
                return self.__error("Last-Event-ID must be an integer", 400)
            if reconnect_version < 0:
                return self.__error("Last-Event-ID must be zero or greater", 400)
            filters["since_version"] = reconnect_version

        initial = self.__query_events(filters)
        if isinstance(initial, HTTPResponse):
            return initial
        initial = self.__bounded_payload(initial, "events", self._MAX_PAGE_LIMIT)
        bottle.response.content_type = "text/event-stream"
        bottle.response.cache_control = "no-cache"
        bottle.response.set_header("X-Content-Type-Options", "nosniff")

        def stream() -> Iterator[str]:
            current = copy.deepcopy(initial)
            last_version = self.__payload_latest_version(current)
            yield self.__sse("snapshot", current, last_version)
            if not follow:
                return
            last_heartbeat = time.monotonic()
            while True:
                time.sleep(interval)
                poll_filters = dict(filters)
                if last_version is not None:
                    poll_filters["since_version"] = last_version
                poll = self.__query_events(poll_filters)
                if isinstance(poll, HTTPResponse):
                    yield self.__sse("error", {"error": "breadcrumb query failed"}, None)
                    continue
                poll = self.__bounded_payload(poll, "events", self._MAX_PAGE_LIMIT)
                events = self.__payload_events(poll)
                if self.__payload_has_reset_or_gap(poll):
                    # A gap/reset is durable cursor metadata, not an empty-page
                    # condition. Send it before any event advances the cursor.
                    yield self.__sse("reset", poll, self.__payload_latest_version(poll))
                    latest = self.__payload_latest_version(poll)
                    if not events and isinstance(latest, int):
                        last_version = max(last_version or latest, latest)
                    last_heartbeat = time.monotonic()
                if events:
                    for event in events:
                        version = event.get("last_seen_version", event.get("version")) if isinstance(event, dict) else None
                        if isinstance(version, int):
                            last_version = max(last_version or version, version)
                        yield self.__sse("breadcrumb", event, version if isinstance(version, int) else None)
                    last_heartbeat = time.monotonic()
                elif not self.__payload_has_reset_or_gap(poll) and time.monotonic() - last_heartbeat >= heartbeat:
                    yield ": keepalive\n\n"
                    last_heartbeat = time.monotonic()

        return stream()

    def __handle_policy_validate(self):
        body = self.__read_json_body()
        if isinstance(body, HTTPResponse):
            return body
        candidate = self.__policy_candidate(body)
        if isinstance(candidate, HTTPResponse):
            return candidate
        result = self.__call_service("validate_policy", {"candidate": candidate})
        return result if isinstance(result, HTTPResponse) else self.__bounded_json_payload(result, None, 1)

    def __handle_policy_apply(self):
        body = self.__read_json_body()
        if isinstance(body, HTTPResponse):
            return body
        candidate = self.__policy_candidate(body)
        if isinstance(candidate, HTTPResponse):
            return candidate
        expected = self.__expected_revision(body)
        if isinstance(expected, HTTPResponse):
            return expected
        service = getattr(self.__context.breadcrumb_trace, "apply_policy", None)
        if not callable(service):
            return self.__error("breadcrumb policy API is unavailable", 501)
        try:
            persist = body.get("persist", False)
            if type(persist) is not bool:
                return self.__error("persist must be a boolean", 400)
            result = self.__invoke(service, {"candidate": candidate, "expected_revision": expected, "persist": persist})
        except Exception as exc:
            return self.__service_error(exc)
        status = 409 if isinstance(result, dict) and result.get("conflict") is True else 200
        return self.__json_response(result, status=status)

    def __handle_policy_reset(self):
        body = self.__read_json_body()
        if isinstance(body, HTTPResponse):
            return body
        unknown = set(body).difference({"expected_revision", "persist"})
        if unknown:
            return self.__error("unknown policy reset field", 400)
        expected = self.__expected_revision(body)
        if isinstance(expected, HTTPResponse):
            return expected
        persist = body.get("persist", False)
        if type(persist) is not bool:
            return self.__error("persist must be a boolean", 400)
        result = self.__call_service("reset_policy", {"expected_revision": expected, "persist": persist})
        if isinstance(result, HTTPResponse):
            return result
        status = 409 if isinstance(result, dict) and result.get("conflict") is True else 200
        return self.__json_response(result, status=status)

    def __handle_clear(self):
        body = self.__read_json_body()
        if isinstance(body, HTTPResponse):
            return body
        if "scope" in body:
            if set(body).difference({"scope"}):
                return self.__error("unknown clear field", 400)
            scope = body["scope"]
        else:
            # A direct filter object is accepted for the collector's scoped
            # clear contract, e.g. {"corr_id": "flow-1"}.
            scope = body if body else None
        if scope is not None and not isinstance(scope, (str, dict)):
            return self.__error("clear scope must be a string or JSON object", 400)
        if isinstance(scope, dict):
            for key, value in scope.items():
                if key not in self._CLEAR_SCOPE_FIELDS:
                    return self.__error("unsupported clear scope key '{}'".format(key), 400)
                if not isinstance(key, str) or len(key) > self._MAX_FILTER_STRING_LENGTH:
                    return self.__error("clear scope keys must be bounded strings", 400)
                if not isinstance(value, (str, int, bool)):
                    return self.__error("clear scope values must be scalar", 400)
                if isinstance(value, str) and (not value or len(value) > self._MAX_FILTER_STRING_LENGTH):
                    return self.__error("clear scope values must be bounded strings", 400)
        result = self.__call_service("clear", {"scope": scope})
        if isinstance(result, HTTPResponse):
            return result
        return self.__bounded_json_payload(result if result is not None else {"status": "cleared"}, None, 1)

    # ------------------------------------------------------------------
    # Request parsing and service compatibility

    def __read_query_filters(self, max_limit: int, default_limit: int):
        query = bottle.request.query
        for key in query.keys():
            if key not in self._QUERY_FIELDS:
                return self.__error("unknown breadcrumb query parameter '{}'".format(key), 400)
            values = query.getall(key)
            if len(values) != 1:
                return self.__error("{} must be provided once".format(key), 400)

        filters: Dict[str, Any] = {}
        integer_fields = {
            "limit", "since_version", "until_version", "version", "start_time_ms",
            "end_time_ms", "created_after_ms", "created_before_ms", "min_time_ms",
            "max_time_ms", "min_version", "max_version", "interval_ms", "heartbeat_ms",
        }
        for key in query.keys():
            value = query.get(key)
            canonical = self._QUERY_ALIASES.get(key, key)
            if key in integer_fields:
                parsed = self.__parse_query_int(key, value)
                if isinstance(parsed, HTTPResponse):
                    return parsed
                if parsed < 0 or (key == "limit" and parsed < 1):
                    return self.__error("{} must be a valid non-negative integer".format(key), 400)
            elif key == "follow":
                parsed = self.__parse_bool(value, key)
                if isinstance(parsed, HTTPResponse):
                    return parsed
            elif key in {"order", "format"}:
                parsed = value.strip().lower()
                if key == "order" and parsed not in {"asc", "desc"}:
                    return self.__error("order must be 'asc' or 'desc'", 400)
            else:
                parsed = value.strip()
                if not parsed or len(parsed) > self._MAX_FILTER_STRING_LENGTH:
                    return self.__error("{} must be a non-empty bounded string".format(key), 400)

            if canonical in filters and filters[canonical] != parsed:
                return self.__error("conflicting values for {}".format(canonical), 400)
            filters[canonical] = parsed

        limit = filters.get("limit", default_limit)
        if limit > max_limit:
            return self.__error("limit must not exceed {}".format(max_limit), 400)
        filters["limit"] = limit
        if (
            isinstance(filters.get("since_version"), int)
            and isinstance(filters.get("until_version"), int)
            and filters["until_version"] < filters["since_version"]
        ):
            return self.__error("until_version must not be less than since_version", 400)
        if "interval_ms" in filters and filters["interval_ms"] < 100:
            return self.__error("interval_ms must be at least 100", 400)
        if filters.get("interval_ms", 0) > int(self._MAX_STREAM_INTERVAL_SECONDS * 1000):
            return self.__error("interval_ms is too large", 400)
        if filters.get("heartbeat_ms", 0) and filters["heartbeat_ms"] < 1000:
            return self.__error("heartbeat_ms must be at least 1000", 400)
        return filters

    def __query_events(self, filters: Mapping[str, Any]):
        if callable(getattr(self.__context.breadcrumb_trace, "query_events", None)):
            # Current and older collectors use corr_id and implement the
            # common dimensions in snapshot().  Keep the richer HTTP filter
            # vocabulary here and apply dimensions unavailable to that local
            # service after the bounded query returns.
            result = self.__call_service("query_events", filters)
            if isinstance(result, HTTPResponse) and self.__has_deep_service_filters(filters):
                result = self.__call_service("query_events", self.__service_query_filters(filters))
            if isinstance(result, HTTPResponse):
                return result
            return self.__apply_deep_filters(result, filters)
        legacy = {
            key: filters[key] for key in (
                "since_version", "limit", "corr_id", "flow_id", "stage", "event_type",
                "path_pair_id", "file_id", "order",
            ) if key in filters
        }
        unsupported = set(filters).difference(legacy)
        if unsupported:
            return self.__error("breadcrumb query API is unavailable", 501)
        snapshot = getattr(self.__context.breadcrumb_trace, "snapshot", None)
        if not callable(snapshot):
            return self.__error("breadcrumb query API is unavailable", 501)
        try:
            return snapshot(**legacy)
        except Exception as exc:
            return self.__service_error(exc)

    @staticmethod
    def __service_query_filters(filters: Mapping[str, Any]) -> Dict[str, Any]:
        service_filters = {
            "since_version": filters.get("since_version"),
            "limit": filters.get("limit"),
            "corr_id": filters.get("correlation_id"),
            "flow_id": filters.get("flow_id"),
            "source": filters.get("source"),
            "category": filters.get("category"),
            "level": filters.get("level"),
            "stage": filters.get("stage"),
            "event_type": filters.get("event_type"),
            "order": filters.get("order", "asc"),
        }
        return {key: value for key, value in service_filters.items() if value is not None}

    @staticmethod
    def __has_deep_service_filters(filters: Mapping[str, Any]) -> bool:
        return any(
            key in filters for key in (
                "category_prefix", "until_version", "version", "start_time_ms", "end_time_ms",
                "correlation_id",
            )
        )

    @classmethod
    def __apply_deep_filters(cls, payload: object, filters: Mapping[str, Any]) -> object:
        """Apply dimensions not present in an older query_events implementation."""
        events = cls.__payload_events(payload)
        if events is None:
            return payload
        selected = []
        for event in events:
            if not isinstance(event, dict):
                continue
            version = event.get("version")
            if isinstance(filters.get("version"), int) and version != filters["version"]:
                continue
            if isinstance(filters.get("until_version"), int) and (
                not isinstance(version, int) or version > filters["until_version"]
            ):
                continue
            category = event.get("category")
            if filters.get("correlation_id") is not None and event.get("corr_id") != filters["correlation_id"]:
                continue
            if filters.get("source") is not None and event.get("source") != filters["source"]:
                continue
            if filters.get("category") is not None and category != filters["category"]:
                continue
            if filters.get("level") is not None and event.get("level") != filters["level"]:
                continue
            if filters.get("category_prefix") is not None and (
                not isinstance(category, str) or not category.startswith(filters["category_prefix"])
            ):
                continue
            created_ms = event.get("created_ms")
            if isinstance(filters.get("start_time_ms"), int) and (
                not isinstance(created_ms, int) or created_ms < filters["start_time_ms"]
            ):
                continue
            if isinstance(filters.get("end_time_ms"), int) and (
                not isinstance(created_ms, int) or created_ms > filters["end_time_ms"]
            ):
                continue
            selected.append(event)
        selected = selected[: int(filters.get("limit", cls._DEFAULT_PAGE_LIMIT))]
        if isinstance(payload, list):
            return selected
        bounded = copy.deepcopy(payload)
        bounded["events"] = selected
        if "entries" in bounded:
            bounded["entries"] = copy.deepcopy(selected)
        if "entry_count" in bounded:
            bounded["entry_count"] = len(selected)
        bounded["query_bounded"] = True
        return bounded

    def __call_service(self, name: str, kwargs: Mapping[str, Any]):
        service = getattr(self.__context.breadcrumb_trace, name, None)
        if not callable(service):
            return self.__error("breadcrumb {} API is unavailable".format(name), 501)
        try:
            return self.__invoke(service, kwargs)
        except Exception as exc:
            return self.__service_error(exc)

    @staticmethod
    def __invoke(service: Callable[..., Any], kwargs: Mapping[str, Any]):
        try:
            signature = inspect.signature(service)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            parameters = signature.parameters
            if not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
                kwargs = {key: value for key, value in kwargs.items() if key in parameters}
        return service(**dict(kwargs))

    def __service_response(self, method: str):
        result = self.__call_service(method, {})
        return result if isinstance(result, HTTPResponse) else self.__bounded_json_payload(result, None, 1)

    # ------------------------------------------------------------------
    # Payload and body helpers

    def __read_json_body(self):
        content_length = bottle.request.content_length
        if content_length is not None and content_length > self._MAX_REQUEST_BYTES:
            return self.__error("request body is too large", 413)
        try:
            raw = bottle.request.body.read(self._MAX_REQUEST_BYTES + 1)
            if len(raw) > self._MAX_REQUEST_BYTES:
                return self.__error("request body is too large", 413)
            value = json.loads(raw.decode("utf-8"), parse_constant=self.__reject_json_constant) if raw.strip() else {}
        except (UnicodeDecodeError, ValueError, TypeError):
            return self.__error("request body must be valid JSON", 400)
        if not isinstance(value, dict):
            return self.__error("request body must be a JSON object", 400)
        return value

    @staticmethod
    def __reject_json_constant(value: str):
        raise ValueError("invalid JSON constant {}".format(value))

    def __policy_candidate(self, body: Mapping[str, Any]):
        wrapper_key = "policy" if "policy" in body else "candidate" if "candidate" in body else None
        if wrapper_key is not None:
            unknown = set(body).difference({wrapper_key, "expected_revision", "persist"})
            if unknown or not isinstance(body[wrapper_key], dict):
                return self.__error("policy must be a JSON object", 400)
            return body[wrapper_key]
        if "expected_revision" in body or "persist" in body:
            candidate = dict(body)
            candidate.pop("expected_revision", None)
            candidate.pop("persist", None)
            if not candidate:
                return self.__error("policy candidate is required", 400)
            return candidate
        return body

    def __expected_revision(self, body: Mapping[str, Any]):
        if "expected_revision" not in body:
            return None
        value = body["expected_revision"]
        if type(value) is not int or value < 0:
            return self.__error("expected_revision must be a non-negative integer", 400)
        return value

    @staticmethod
    def __parse_query_int(name: str, value: str):
        try:
            return int(value)
        except (TypeError, ValueError):
            return BreadcrumbTraceHandler.__error("{} must be an integer".format(name), 400)

    @staticmethod
    def __parse_bool(value: str, name: str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
        return BreadcrumbTraceHandler.__error("{} must be a boolean".format(name), 400)

    @staticmethod
    def __json_response(payload: object, status: int = 200) -> HTTPResponse:
        try:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            return BreadcrumbTraceHandler.__error("breadcrumb response is not valid JSON", 500)
        return HTTPResponse(body=body, status=status, headers=BreadcrumbTraceHandler._JSON_HEADERS)

    @classmethod
    def __bounded_json_payload(cls, payload: object, event_key: Optional[str], max_events: int):
        bounded = cls.__bounded_payload(payload, event_key, max_events)
        response = cls.__json_response(bounded)
        if isinstance(response, HTTPResponse) and len(response.body) > cls._MAX_RESPONSE_BYTES:
            return cls.__error("breadcrumb response exceeds the bounded response size", 413)
        return response

    @classmethod
    def __bounded_payload(cls, payload: object, event_key: Optional[str], max_events: int) -> object:
        if not isinstance(payload, (dict, list)):
            return {"result": payload}
        bounded = copy.deepcopy(payload)
        if event_key is None:
            return bounded
        events = cls.__payload_events(bounded)
        if events is None:
            return bounded
        if len(events) > max_events:
            events = events[:max_events]
            if isinstance(bounded, dict):
                bounded["events"] = events
                if isinstance(bounded.get("entries"), list):
                    bounded["entries"] = copy.deepcopy(events)
                bounded["entry_count"] = len(events)
                bounded["response_truncated"] = True
            else:
                bounded = events
        return bounded

    @staticmethod
    def __payload_events(payload: object) -> Optional[list]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("events", "entries"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return None

    @classmethod
    def __payload_latest_version(cls, payload: object) -> Optional[int]:
        latest = payload.get("version") if isinstance(payload, dict) else None
        latest = latest if isinstance(latest, int) else None
        for event in cls.__payload_events(payload) or []:
            if isinstance(event, dict) and isinstance(event.get("version"), int):
                latest = max(latest or event["version"], event["version"])
        return latest

    @staticmethod
    def __payload_has_reset_or_gap(payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        return bool(
            payload.get("window_reset") or payload.get("gap") or payload.get("gap_detected")
            or payload.get("reset") or payload.get("window_truncated")
        )

    @staticmethod
    def __sse(event_name: str, payload: object, version: Optional[int]) -> str:
        data = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        if len(data.encode("utf-8")) > BreadcrumbTraceHandler._MAX_RESPONSE_BYTES:
            data = json.dumps({"truncated": True, "error": "breadcrumb frame exceeds bound"}, separators=(",", ":"))
        event_id = "id: {}\n".format(version) if isinstance(version, int) else ""
        return "{}event: {}\ndata: {}\n\n".format(event_id, event_name, data)

    @staticmethod
    def __error(message: str, status: int) -> HTTPResponse:
        return HTTPResponse(
            body=json.dumps({"error": message}, separators=(",", ":")),
            status=status,
            headers=BreadcrumbTraceHandler._JSON_HEADERS,
        )

    @classmethod
    def __service_error(cls, exc: Exception) -> HTTPResponse:
        message = str(exc) or "breadcrumb service request failed"
        lowered = message.lower()
        status = 409 if "conflict" in lowered or ("revision" in lowered and "mismatch" in lowered) else 400
        return cls.__error(message, status)

    @staticmethod
    def __parse_optional_string(name: str):
        value = getattr(bottle.request.query, "get")(name)
        if value is None or value == "":
            return None
        return value

    @staticmethod
    def __parse_optional_int(name: str):
        value = getattr(bottle.request.query, "get")(name)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except ValueError:
            return HTTPResponse(body="{} must be an integer".format(name), status=400)
