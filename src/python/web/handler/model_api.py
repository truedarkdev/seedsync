# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Versioned, pair-scoped model delivery without recursive model streaming."""

from __future__ import annotations

import base64
from collections import OrderedDict
import json
import time
from threading import Event, Lock
from typing import Iterator, Optional

import bottle
from bottle import HTTPResponse

from common import BreadcrumbTraceCollector, PerformanceDiagnosticsCollector, overrides
from common.breadcrumb_trace import opaque_trace_correlation
from common.root_progress_trace import duration_bucket, root_progress_tracer
from common.performance_diagnostics import (
    DURATION_MODEL_SCOPED_SERIALIZATION,
    DURATION_MODEL_SCOPED_SSE_EMISSION,
    DURATION_MODEL_SUMMARY_SERIALIZATION,
    DURATION_MODEL_SUMMARY_SSE_EMISSION,
)
from controller import Controller
from controller.controller import (
    MODEL_LEGACY_SCOPE_ID,
    ModelPageCursorError,
)
from model import IModelListener, ModelFile
from ..web_app import IHandler, WebApp


class _CursorContractError(ValueError):
    """A well-formed cursor belongs to a different scoped request."""


class ScopedModelListener(IModelListener):
    """Coalesce only primitive identities for one model scope.

    It intentionally implements the old listener methods as no-ops: Model
    invokes ``model_version_changed`` after every mutation with no ModelFile
    reference retained by this object.
    """

    # Root patch payloads share the transport page cap. Since every queued
    # identity maps to at most one deduplicated root, 200 identities ensures
    # records + removed_file_ids can never exceed one bounded response.
    _MAX_IDENTITIES = 200
    _MAX_IDENTITY_BYTES = 32 * 1024

    def __init__(self, scope_id: str):
        self.__scope_id = scope_id
        self.__changes: OrderedDict[str, int] = OrderedDict()
        self.__global_versions: dict[str, int] = {}
        self.__reset_version: Optional[int] = None
        self.__reset_origin_global_version: Optional[int] = None
        self.__identity_bytes = 0
        self.__closed = False
        self.__lock = Lock()
        self.__available = Event()

    def file_added(self, file: ModelFile) -> None:
        pass

    def file_removed(self, file: ModelFile) -> None:
        pass

    def file_updated(self, old_file: ModelFile, new_file: ModelFile) -> None:
        pass

    def model_version_changed(
        self, version: int, path_pair_id: Optional[str], file_id: str
    ) -> None:
        # Legacy callbacks remain part of Model's listener contract. Exact
        # scoped delivery uses the atomic additive callback below instead.
        del version, path_pair_id, file_id

    def model_version_published(
        self, version: int, global_version: int, path_pair_id: Optional[str], file_id: str,
    ) -> None:
        """Queue scope/global origin and availability in one lock operation."""
        scope_id = path_pair_id if path_pair_id is not None else MODEL_LEGACY_SCOPE_ID
        if scope_id != self.__scope_id:
            return
        with self.__lock:
            if self.__closed:
                return
            if self.__reset_version is not None:
                self.__reset_version = max(self.__reset_version, version)
                self.__reset_origin_global_version = global_version if type(global_version) is int else None
                return
            existing = self.__changes.pop(file_id, None)
            if existing is not None:
                self.__identity_bytes -= len(file_id.encode("utf-8"))
                self.__global_versions.pop(file_id, None)
            self.__changes[file_id] = version
            self.__global_versions[file_id] = global_version if type(global_version) is int else -1
            self.__identity_bytes += len(file_id.encode("utf-8"))
            if (
                len(self.__changes) > self._MAX_IDENTITIES
                or self.__identity_bytes > self._MAX_IDENTITY_BYTES
            ):
                self.__changes.clear()
                self.__global_versions.clear()
                self.__identity_bytes = 0
                self.__reset_version = version
                self.__reset_origin_global_version = global_version if type(global_version) is int else None
            self.__available.set()

    def take_next_event(self) -> Optional[dict[str, object]]:
        with self.__lock:
            if self.__closed:
                return None
            if self.__reset_version is not None:
                version = self.__reset_version
                origin_global_version = self.__reset_origin_global_version
                self.__reset_version = None
                self.__reset_origin_global_version = None
                self.__global_versions.clear()
                self.__available.clear()
                return {
                    "event": "model-reset", "model_version": version, "reason": "coalesced",
                    "_origin_global_model_version": origin_global_version,
                }
            if not self.__changes:
                return None
            changes = list(self.__changes.items())
            origin_global_version = max(
                (self.__global_versions.get(file_id, -1) for file_id, _ in changes), default=-1,
            )
            self.__changes.clear()
            self.__global_versions.clear()
            self.__identity_bytes = 0
            self.__available.clear()
        return {
            "event": "model-invalidate",
            "model_version": max(version for _, version in changes),
            "file_ids": [file_id for file_id, _ in changes],
            "_origin_global_model_version": origin_global_version if origin_global_version >= 0 else None,
        }

    def wait_for_event(self, timeout: float) -> bool:
        return self.__available.wait(timeout=max(0.0, timeout))

    def close(self) -> None:
        with self.__lock:
            self.__closed = True
            self.__changes.clear()
            self.__global_versions.clear()
            self.__identity_bytes = 0
            self.__reset_version = None
            self.__reset_origin_global_version = None
            self.__available.set()


class SummaryModelListener(IModelListener):
    """Bounded coalescer for summary-only SSE; never retains file records."""
    _MAX_SCOPES = 128

    def __init__(self):
        self.__scopes: OrderedDict[str, int] = OrderedDict()
        self.__reset = False
        self.__closed = False
        self.__lock = Lock()
        self.__available = Event()

    def file_added(self, file: ModelFile) -> None: pass
    def file_removed(self, file: ModelFile) -> None: pass
    def file_updated(self, old_file: ModelFile, new_file: ModelFile) -> None: pass

    def model_version_changed(self, version: int, path_pair_id: Optional[str], file_id: str) -> None:
        del file_id
        scope = path_pair_id if path_pair_id is not None else MODEL_LEGACY_SCOPE_ID
        with self.__lock:
            if self.__closed:
                return
            if self.__reset:
                return
            self.__scopes[scope] = version
            self.__scopes.move_to_end(scope)
            if len(self.__scopes) > self._MAX_SCOPES:
                self.__scopes.clear()
                self.__reset = True
            self.__available.set()

    def model_summary_changed(self) -> None:
        with self.__lock:
            if self.__closed:
                return
            self.__reset = True
            self.__available.set()

    def take_next_event(self) -> Optional[dict[str, object]]:
        with self.__lock:
            if self.__closed:
                return None
            if self.__reset:
                self.__reset = False
                self.__available.clear()
                return {"event": "model-summary-reset", "reason": "coalesced"}
            if not self.__scopes:
                return None
            scopes = list(self.__scopes)
            self.__scopes.clear()
            self.__available.clear()
            return {"event": "model-summary-update", "path_pair_ids": scopes}

    def wait_for_event(self, timeout: float) -> bool:
        return self.__available.wait(timeout=max(0.0, timeout))

    def close(self) -> None:
        with self.__lock:
            self.__closed = True
            self.__scopes.clear()
            self.__available.set()


class ModelApiHandler(IHandler):
    """Routes for paged model reads and scoped, lightweight invalidations."""

    _DEFAULT_LIMIT = 100
    _MAX_LIMIT = 200
    _MAX_CURSOR_LENGTH = 4096
    _KEEPALIVE_INTERVAL_SECONDS = 5.0
    _SUMMARY_MIN_INTERVAL_SECONDS = 0.5

    def __init__(
        self,
        controller: Controller,
        performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None,
        breadcrumb_trace: Optional[BreadcrumbTraceCollector] = None,
    ):
        self.__controller = controller
        self.__performance_diagnostics = performance_diagnostics
        self.__breadcrumb_trace = breadcrumb_trace

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_handler(
            "/server/model/v1/summary", self.__handle_summary,
            required_scope="read", allow_sessionless_ui=True,
        )
        web_app.get(
            "/server/model/v1/summary/stream",
            required_scope="stream", allow_sessionless_ui=True,
        )(self.__handle_summary_stream)
        web_app.get(
            "/server/model/v1/pairs/<path_pair_id>/roots",
            required_scope="read", allow_sessionless_ui=True,
        )(self.__handle_roots)
        web_app.get(
            "/server/model/v1/pairs/<path_pair_id>/children",
            required_scope="read", allow_sessionless_ui=True,
        )(self.__handle_children)
        web_app.get(
            "/server/model/v1/pairs/<path_pair_id>/stream",
            required_scope="stream", allow_sessionless_ui=True,
        )(self.__handle_stream)

    def __record_duration(self, metric: str, operation) -> object:
        diagnostics = self.__performance_diagnostics
        started_at = None
        try:
            started_at = diagnostics.begin_duration(metric) if diagnostics is not None else None
        except Exception:
            pass
        try:
            return operation()
        finally:
            if diagnostics is not None:
                try:
                    diagnostics.finish_duration(metric, started_at)
                except Exception:
                    pass

    def __json_response(
            self, payload: object, status: int = 200, *, summary: bool = False,
            scope_id: Optional[str] = None,
    ) -> HTTPResponse:
        metric = DURATION_MODEL_SUMMARY_SERIALIZATION if summary else DURATION_MODEL_SCOPED_SERIALIZATION
        counter = "model_summary_serializations" if summary else "model_page_serializations"
        diagnostics = self.__performance_diagnostics
        if diagnostics is not None:
            try:
                diagnostics.increment(counter)
            except Exception:
                pass
        progress_trace = root_progress_tracer(self.__breadcrumb_trace)
        progress_started_ns: Optional[int] = None
        serialization_start_admitted = False
        payload_model_version = payload.get("model_version") if isinstance(payload, dict) else None
        payload_global_model_version = payload.get("_global_model_version") if isinstance(payload, dict) else None
        transport_payload = payload
        if isinstance(payload, dict) and "_global_model_version" in payload:
            transport_payload = dict(payload)
            transport_payload.pop("_global_model_version", None)
        if scope_id is not None and progress_trace is not None and progress_trace.enabled("debug"):
            if progress_trace.record(
                    scope_id,
                    "serialization_start",
                    {
                        "scope_digest": opaque_trace_correlation(scope_id),
                        "model_version": payload_global_model_version if type(payload_global_model_version) is int else None,
                        "scope_version": payload_model_version if type(payload_model_version) is int else None,
                        "publication": "scoped",
                        "event": "model-page",
                        "outcome": "started",
                        "reason": "page_json_serialization",
                    },
            ):
                serialization_start_admitted = True

        def serialize_json() -> str:
            nonlocal progress_started_ns
            if serialization_start_admitted:
                progress_started_ns = time.monotonic_ns()
            return json.dumps(transport_payload)

        body = self.__record_duration(metric, serialize_json)
        if progress_started_ns is not None and progress_trace is not None:
            duration_ms = max(0, int((time.monotonic_ns() - progress_started_ns) / 1_000_000))
            records = payload.get("records") if isinstance(payload, dict) else None
            model_version = payload_model_version
            progress_trace.record(
                scope_id,
                "serialization_end",
                {
                    "scope_digest": opaque_trace_correlation(scope_id),
                    "model_version": payload_global_model_version if type(payload_global_model_version) is int else None,
                    "scope_version": model_version if type(model_version) is int else None,
                    "publication": "scoped",
                    "event": "model-page",
                    "root_count": payload.get("total") if isinstance(payload, dict) and type(payload.get("total")) is int else 0,
                    "page_count": len(records) if isinstance(records, list) else 0,
                    "record_count": len(records) if isinstance(records, list) else 0,
                    "outcome": "error" if isinstance(payload, dict) and payload.get("error") else "completed",
                    "reason": "cursor_reset" if isinstance(payload, dict) and payload.get("error") == "cursor_reset_required" else "page_json_serialized",
                    "duration_ms": duration_ms,
                    "duration_bucket": duration_bucket(duration_ms),
                },
            )
        return HTTPResponse(body=body, status=status, headers={"Content-Type": "application/json"})

    def __sse(
        self, publication: str, event: str, payload: dict[str, object], version: Optional[int] = None,
        global_model_version: Optional[int] = None,
        scope_id: Optional[str] = None, lineage_model_version: Optional[int] = None,
    ) -> str:
        serialization_metric = DURATION_MODEL_SUMMARY_SERIALIZATION if publication == "summary" \
            else DURATION_MODEL_SCOPED_SERIALIZATION
        emission_metric = DURATION_MODEL_SUMMARY_SSE_EMISSION if publication == "summary" \
            else DURATION_MODEL_SCOPED_SSE_EMISSION
        counter = "model_summary_sse_emissions" if publication == "summary" else "model_scoped_sse_emissions"

        def format_event() -> str:
            nonlocal progress_started_ns
            if serialization_start_admitted:
                progress_started_ns = time.monotonic_ns()
            event_id = "id: {}\n".format(version) if version is not None else ""
            return "{}event: {}\ndata: {}\n\n".format(
                event_id, event, json.dumps(payload, separators=(",", ":"))
            )

        progress_trace = root_progress_tracer(self.__breadcrumb_trace)
        scoped = publication == "scoped"
        progress_identity = scope_id if scoped else "summary"
        progress_started_ns: Optional[int] = None
        serialization_start_admitted = False
        scoped_version = version if scoped and isinstance(version, int) else None
        captured_global_version = (
            global_model_version if scoped and isinstance(global_model_version, int)
            else version if not scoped and isinstance(version, int) else None
        )
        if (not scoped or scope_id is not None) and progress_trace is not None and progress_trace.enabled("debug"):
            if progress_trace.record(
                    progress_identity,
                    "serialization_start",
                    {
                        "scope_digest": opaque_trace_correlation(progress_identity),
                        "publication": "scoped" if scoped else "summary",
                        "model_version": captured_global_version,
                        "scope_version": scoped_version,
                        "event": event,
                        "outcome": "started",
                        "reason": "sse_json_serialization",
                    },
            ):
                serialization_start_admitted = True
        rendered = self.__record_duration(serialization_metric, format_event)
        if progress_started_ns is not None and progress_trace is not None:
            duration_ms = max(0, int((time.monotonic_ns() - progress_started_ns) / 1_000_000))
            progress_trace.record(
                progress_identity,
                "serialization_end",
                {
                    "scope_digest": opaque_trace_correlation(progress_identity),
                    "model_version": captured_global_version,
                    "scope_version": scoped_version,
                    "publication": "scoped" if scoped else "summary",
                    "event": event,
                    "outcome": "completed",
                    "reason": "sse_json_serialized",
                    "duration_ms": duration_ms,
                    "duration_bucket": duration_bucket(duration_ms),
                },
            )
        emission_started_ns = time.monotonic_ns()

        def publish() -> str:
            diagnostics = self.__performance_diagnostics
            if diagnostics is not None:
                try:
                    diagnostics.increment(counter)
                except Exception:
                    pass
            trace = self.__breadcrumb_trace
            if trace is not None:
                try:
                    global_version = version if publication == "summary" else global_model_version
                    if trace.is_effectively_enabled("model_api", "info") and isinstance(global_version, int):
                        correlation = "model_version:{}".format(global_version)
                        details = {"model_version": global_version}
                        if publication == "scoped" and isinstance(version, int):
                            details["scope_version"] = version
                        trace.record(
                            "model_api",
                            "model_summary_sse_published" if publication == "summary" else "model_scoped_sse_published",
                            details,
                            stage="model_publication",
                            event_type="state_transition",
                            corr_id=correlation,
                            flow_id=correlation,
                            trace_scope="aggregate",
                        )
                except Exception:
                    pass
            progress_trace = root_progress_tracer(trace)
            if progress_trace is not None and progress_trace.enabled("debug"):
                try:
                    captured_global_version = version if publication == "summary" and isinstance(version, int) else global_model_version
                    if not isinstance(captured_global_version, int):
                        captured_global_version = None
                    if not scoped or scope_id is not None:
                        if scoped:
                            lineage_recorder = getattr(trace, "record_progress_lineage_for_model_version", None)
                            if callable(lineage_recorder) and isinstance(lineage_model_version, int):
                                lineage_recorder(
                                    lineage_model_version, "scoped_stream_emit",
                                    {
                                        "model_version": captured_global_version,
                                        "scope_version": version if isinstance(version, int) else None,
                                        "duration_bucket": duration_bucket(
                                            max(0, int((time.monotonic_ns() - emission_started_ns) / 1_000_000))
                                        ),
                                    },
                                )
                        progress_trace.record(
                            progress_identity,
                            "sse",
                            {
                            "scope_digest": opaque_trace_correlation(progress_identity),
                            "model_version": captured_global_version,
                            "scope_version": version if scoped and isinstance(version, int) else None,
                            "publication": "scoped" if scoped else "summary",
                            "representation": "scoped_sse" if scoped else "summary_sse",
                            "event": event,
                            "outcome": "emitted",
                            "reason": "sse_write",
                            "duration_ms": max(0, int((time.monotonic_ns() - emission_started_ns) / 1_000_000)),
                            "duration_bucket": duration_bucket(
                                max(0, int((time.monotonic_ns() - emission_started_ns) / 1_000_000))
                            ),
                            },
                        )
                except Exception:
                    pass
            return rendered

        return self.__record_duration(emission_metric, publish)  # type: ignore[return-value]

    @staticmethod
    def __validate_scope_id(path_pair_id: str) -> str:
        if not isinstance(path_pair_id, str) or not path_pair_id or len(path_pair_id) > 128:
            bottle.abort(400, "path_pair_id is required and must be bounded")
        if any(ord(char) < 32 for char in path_pair_id):
            bottle.abort(400, "path_pair_id is invalid")
        return path_pair_id

    @classmethod
    def __read_limit(cls) -> int:
        raw_limit = bottle.request.query.get("limit")
        if raw_limit is None or raw_limit == "":
            return cls._DEFAULT_LIMIT
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            bottle.abort(400, "limit must be an integer")
        if limit < 1 or limit > cls._MAX_LIMIT:
            bottle.abort(400, "limit must be between 1 and {}".format(cls._MAX_LIMIT))
        return limit

    @staticmethod
    def __read_parent_file_id() -> str:
        parent_file_id = bottle.request.query.get("parent_file_id")
        if not isinstance(parent_file_id, str) or not parent_file_id or len(parent_file_id) > 4096:
            bottle.abort(400, "parent_file_id is required and must be bounded")
        return parent_file_id

    @staticmethod
    def __read_query() -> tuple[int, Optional[str], Optional[str], str]:
        raw_sort = bottle.request.query.get("sort", "1")
        try:
            sort_mode = int(raw_sort)
        except (TypeError, ValueError):
            bottle.abort(400, "sort is invalid")
        if sort_mode not in set(range(13)):
            bottle.abort(400, "sort is invalid")
        status = bottle.request.query.get("status") or None
        allowed_statuses = {"default", "queued", "downloading", "downloaded", "stopped", "deleted",
                            "extracting", "extracted", "validating", "validated", "corrupt", "move_failed",
                            "move_succeeded", "local_only"}
        if status is not None and status not in allowed_statuses:
            bottle.abort(400, "status is invalid")
        name = bottle.request.query.get("name") or None
        if name is not None and (len(name) > 256 or any(ord(char) < 32 for char in name)):
            bottle.abort(400, "name is invalid")
        # Root transport order is deliberately canonical file identity. These
        # legacy UI parameters are validated for compatibility but are not a
        # cursor boundary or server-side presentation contract.
        signature = "identity-v1"
        return sort_mode, status, name, signature

    @classmethod
    def __decode_cursor(
        cls, raw_cursor: Optional[str], scope_id: str, parent_file_id: Optional[str], query_signature: str
    ) -> tuple[Optional[str], Optional[int], Optional[tuple[object, ...]]]:
        if raw_cursor is None or raw_cursor == "":
            return None, None, None
        if not isinstance(raw_cursor, str) or len(raw_cursor) > cls._MAX_CURSOR_LENGTH:
            bottle.abort(400, "cursor is invalid")
        try:
            padded = raw_cursor + "=" * (-len(raw_cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except (UnicodeEncodeError, ValueError, json.JSONDecodeError):
            bottle.abort(400, "cursor is invalid")
        if not isinstance(payload, dict) or not isinstance(payload.get("after"), str) or \
                type(payload.get("version")) is not int or not isinstance(payload.get("after_key"), list) or \
                not all(type(value) in {str, int, float} for value in payload["after_key"]):
            bottle.abort(400, "cursor is invalid")
        if payload.get("scope") != scope_id or payload.get("parent") != parent_file_id or \
                payload.get("query") != query_signature:
            raise _CursorContractError("cursor does not match this model page")
        return payload["after"], payload["version"], tuple(payload["after_key"])

    @staticmethod
    def __encode_cursor(
        version: int, scope_id: str, parent_file_id: Optional[str], after_file_id: Optional[str],
        after_sort_key: Optional[tuple[object, ...]], query_signature: str,
    ) -> Optional[str]:
        if after_file_id is None:
            return None
        payload = json.dumps({
            "version": version, "scope": scope_id,
            "parent": parent_file_id, "after": after_file_id,
            "after_key": list(after_sort_key or ()), "query": query_signature,
        }, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def __public_page(
        cls, page: dict[str, object], query_signature: str, *, preserve_global_model_version: bool = False,
        preserve_stream_authority_projection: bool = False,
    ) -> dict[str, object]:
        next_cursor_file_id = page.pop("next_cursor_file_id", None)
        next_cursor_sort_key = page.pop("next_cursor_sort_key", None)
        version = page.get("model_version")
        scope_id = page.get("path_pair_id")
        parent_file_id = page.get("parent_file_id")
        assert isinstance(version, int) and isinstance(scope_id, str)
        page["next_cursor"] = cls.__encode_cursor(
            version, scope_id,
            parent_file_id if isinstance(parent_file_id, str) else None,
            next_cursor_file_id if isinstance(next_cursor_file_id, str) else None,
            next_cursor_sort_key if isinstance(next_cursor_sort_key, tuple) else None,
            query_signature,
        )
        if not preserve_global_model_version:
            page.pop("_global_model_version", None)
        if not preserve_stream_authority_projection:
            page.pop("_scan_authority_projection", None)
        return page

    def __get_page(
        self, scope_id: str, parent_file_id: Optional[str], *, add_listener: Optional[ScopedModelListener] = None,
        preserve_global_model_version: bool = False, preserve_stream_authority_projection: bool = False,
    ) -> dict[str, object]:
        limit = self.__read_limit()
        sort_mode, status_filter, name_filter, query_signature = self.__read_query()
        try:
            cursor_file_id, cursor_version, cursor_sort_key = self.__decode_cursor(
                bottle.request.query.get("cursor"), scope_id, parent_file_id, query_signature
            )
        except _CursorContractError:
            return {"error": "cursor_reset_required"}
        try:
            if add_listener is None:
                page = self.__controller.get_model_page(
                    scope_id, limit, cursor_file_id, cursor_version, cursor_sort_key, parent_file_id, sort_mode, status_filter, name_filter
                )
            else:
                page = self.__controller.get_model_page_and_add_listener(
                    add_listener, scope_id, limit, cursor_file_id, cursor_version, cursor_sort_key, parent_file_id, sort_mode, status_filter, name_filter
                )
        except ModelPageCursorError:
            return {"error": "invalid_model_cursor"}
        return self.__public_page(
            page, query_signature, preserve_global_model_version=True,
            preserve_stream_authority_projection=preserve_stream_authority_projection,
        )

    def __handle_summary(self) -> HTTPResponse:
        # A fresh multi-pair scan can hold the model publication boundary long
        # enough to exceed clients' admission budget.  Fail explicitly rather
        # than letting the web worker retain an ambiguous, timed-out request.
        model_lock = getattr(self.__controller, "_Controller__model_lock", None)
        if model_lock is not None and not model_lock.acquire(timeout=0.25):
            return self.__json_response({"error": "model_summary_busy"}, status=503, summary=True)
        try:
            summary = self.__controller.get_model_summary()
        finally:
            if model_lock is not None:
                model_lock.release()
        return self.__json_response(summary, summary=True)

    def __handle_summary_stream(self) -> Iterator[str]:
        listener = SummaryModelListener()
        summary = self.__controller.get_model_summary_and_add_listener(listener)
        reconnect_id = bottle.request.get_header("Last-Event-ID", "").strip()
        bottle.response.content_type = "text/event-stream"
        bottle.response.cache_control = "no-cache"

        def stream() -> Iterator[str]:
            try:
                version = summary.get("model_version")
                last_keepalive_at = time.monotonic()
                last_summary_at = time.monotonic()
                pending_event: Optional[dict[str, object]] = None
                yield self.__sse("summary", "model-summary", summary, version if isinstance(version, int) else None)
                if reconnect_id:
                    # Replay is unavailable, but the just-emitted snapshot is
                    # already the complete normalized recovery payload.
                    yield self.__sse("summary", "model-summary", summary, version if isinstance(version, int) else None)
                while True:
                    event = listener.take_next_event()
                    if event is not None:
                        if pending_event is None or event.get("event") == "model-summary-reset":
                            pending_event = event
                        elif pending_event.get("event") == "model-summary-update":
                            existing = pending_event.get("path_pair_ids")
                            incoming = event.get("path_pair_ids")
                            merged = set(existing if isinstance(existing, list) else [])
                            merged.update(incoming if isinstance(incoming, list) else [])
                            pending_event["path_pair_ids"] = sorted(merged)
                    if pending_event is not None and (
                        time.monotonic() - last_summary_at >= self._SUMMARY_MIN_INTERVAL_SECONDS
                    ):
                        del pending_event
                        snapshot = self.__controller.get_model_summary(
                            max_age_seconds=self._SUMMARY_MIN_INTERVAL_SECONDS
                        )
                        update_version = snapshot.get("model_version") if isinstance(snapshot, dict) else None
                        yield self.__sse("summary", "model-summary", snapshot,
                                         update_version if isinstance(update_version, int) else None)
                        pending_event = None
                        last_summary_at = time.monotonic()
                        last_keepalive_at = time.monotonic()
                    elif time.monotonic() - last_keepalive_at >= self._KEEPALIVE_INTERVAL_SECONDS:
                        yield ": keepalive\n\n"
                        last_keepalive_at = time.monotonic()
                    else:
                        now = time.monotonic()
                        deadlines = [last_keepalive_at + self._KEEPALIVE_INTERVAL_SECONDS]
                        if pending_event is not None:
                            deadlines.append(last_summary_at + self._SUMMARY_MIN_INTERVAL_SECONDS)
                        listener.wait_for_event(min(deadlines) - now)
            finally:
                listener.close()
                self.__controller.remove_model_listener(listener)
        return stream()

    def __handle_roots(self, path_pair_id: str) -> HTTPResponse:
        scope_id = self.__validate_scope_id(path_pair_id)
        page = self.__get_page(scope_id, None)
        response = self.__json_response(
            page, 409 if page.get("error") == "cursor_reset_required" else 400 if page.get("error") else 200,
            scope_id=scope_id,
        )
        return response

    def __handle_children(self, path_pair_id: str) -> HTTPResponse:
        scope_id = self.__validate_scope_id(path_pair_id)
        parent_file_id = self.__read_parent_file_id()
        page = self.__get_page(scope_id, parent_file_id)
        return self.__json_response(
            page, 409 if page.get("error") == "cursor_reset_required" else 400 if page.get("error") else 200,
            scope_id=scope_id,
        )

    def __handle_stream(self, path_pair_id: str) -> Iterator[str]:
        scope_id = self.__validate_scope_id(path_pair_id)
        prioritize = getattr(self.__controller, "prioritize_path_pair_scan", None)
        if callable(prioritize):
            prioritize(scope_id)
        listener = ScopedModelListener(scope_id)
        page = self.__get_page(
            scope_id, None, add_listener=listener, preserve_global_model_version=True,
            preserve_stream_authority_projection=True,
        )
        if page.get("error"):
            listener.close()
            return self.__json_response(
                page, 409 if page.get("error") == "cursor_reset_required" else 400,
                scope_id=scope_id,
            )
        trace_scoped_stream = getattr(self.__controller, "record_scoped_model_stream_breadcrumb", None)
        if callable(trace_scoped_stream):
            trace_scoped_stream("atomic_registered", scope_id, page)
        reconnect_id = bottle.request.get_header("Last-Event-ID", "").strip()
        bottle.response.content_type = "text/event-stream"
        bottle.response.cache_control = "no-cache"

        def stream() -> Iterator[str]:
            try:
                version = page.get("model_version")
                global_model_version = page.pop("_global_model_version", None)
                last_keepalive_at = time.monotonic()
                if callable(trace_scoped_stream):
                    trace_scoped_stream("initial_page_emitted", scope_id, page)
                page.pop("_scan_authority_projection", None)
                yield self.__sse(
                    "scoped", "model-page", page, version if isinstance(version, int) else None,
                    global_model_version if isinstance(global_model_version, int) else None,
                    scope_id, global_model_version if isinstance(global_model_version, int) else None,
                )
                if reconnect_id:
                    yield self.__sse(
                        "scoped", "model-reset",
                        {"model_version": version, "reason": "replay_unavailable"},
                        version if isinstance(version, int) else None,
                        global_model_version if isinstance(global_model_version, int) else None,
                        scope_id,
                    )
                while True:
                    event = listener.take_next_event()
                    if event is not None:
                        event_name = event.pop("event")
                        # Listener versions are captured at model mutation;
                        # page assembly below may observe a later global
                        # version. Retain this origin only for exact lineage.
                        event_origin_global_model_version = event.pop("_origin_global_model_version", None)
                        event_global_model_version = None
                        if event_name == "model-invalidate":
                            changed_ids = event.get("file_ids")
                            updates = self.__controller.get_model_root_updates(
                                scope_id,
                                changed_ids if isinstance(changed_ids, list) else [],
                            )
                            event["records"] = updates["records"]
                            event["removed_file_ids"] = updates["removed_file_ids"]
                            event["model_version"] = updates["model_version"]
                            captured = updates.get("_global_model_version")
                            event_global_model_version = captured if isinstance(captured, int) else None
                        elif event_name == "model-reset":
                            versions = self.__controller.get_model_scope_version_snapshot(scope_id)
                            event["model_version"] = versions["model_version"]
                            captured = versions.get("_global_model_version")
                            event_global_model_version = captured if isinstance(captured, int) else None
                        event_version = event.get("model_version")
                        yield self.__sse(
                            "scoped", event_name if isinstance(event_name, str) else "model-reset",
                            event,
                            event_version if isinstance(event_version, int) else None,
                            event_global_model_version,
                            scope_id,
                            event_origin_global_model_version if isinstance(event_origin_global_model_version, int) else None,
                        )
                        last_keepalive_at = time.monotonic()
                    elif time.monotonic() - last_keepalive_at >= self._KEEPALIVE_INTERVAL_SECONDS:
                        # Comments are ignored by EventSource clients but keep
                        # idle proxies from discarding an otherwise healthy
                        # scoped subscription.
                        yield ": keepalive\n\n"
                        last_keepalive_at = time.monotonic()
                    else:
                        listener.wait_for_event(
                            last_keepalive_at + self._KEEPALIVE_INTERVAL_SECONDS - time.monotonic()
                        )
            finally:
                listener.close()
                self.__controller.remove_model_listener(listener)

        return stream()
