# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Versioned, pair-scoped model delivery without recursive model streaming."""

from __future__ import annotations

import base64
from collections import OrderedDict
import json
import time
from threading import Lock
from typing import Iterator, Optional

import bottle
from bottle import HTTPResponse

from common import PerformanceDiagnosticsCollector, overrides
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
        self.__reset_version: Optional[int] = None
        self.__identity_bytes = 0
        self.__closed = False
        self.__lock = Lock()

    def file_added(self, file: ModelFile) -> None:
        pass

    def file_removed(self, file: ModelFile) -> None:
        pass

    def file_updated(self, old_file: ModelFile, new_file: ModelFile) -> None:
        pass

    def model_version_changed(
        self, version: int, path_pair_id: Optional[str], file_id: str
    ) -> None:
        scope_id = path_pair_id if path_pair_id is not None else MODEL_LEGACY_SCOPE_ID
        if scope_id != self.__scope_id:
            return
        with self.__lock:
            if self.__closed:
                return
            if self.__reset_version is not None:
                self.__reset_version = max(self.__reset_version, version)
                return
            existing = self.__changes.pop(file_id, None)
            if existing is not None:
                self.__identity_bytes -= len(file_id.encode("utf-8"))
            self.__changes[file_id] = version
            self.__identity_bytes += len(file_id.encode("utf-8"))
            if (
                len(self.__changes) > self._MAX_IDENTITIES
                or self.__identity_bytes > self._MAX_IDENTITY_BYTES
            ):
                self.__changes.clear()
                self.__identity_bytes = 0
                self.__reset_version = version

    def take_next_event(self) -> Optional[dict[str, object]]:
        with self.__lock:
            if self.__closed:
                return None
            if self.__reset_version is not None:
                version = self.__reset_version
                self.__reset_version = None
                return {"event": "model-reset", "model_version": version, "reason": "coalesced"}
            if not self.__changes:
                return None
            changes = list(self.__changes.items())
            self.__changes.clear()
            self.__identity_bytes = 0
        return {
            "event": "model-invalidate",
            "model_version": max(version for _, version in changes),
            "file_ids": [file_id for file_id, _ in changes],
        }

    def close(self) -> None:
        with self.__lock:
            self.__closed = True
            self.__changes.clear()
            self.__identity_bytes = 0
            self.__reset_version = None


class SummaryModelListener(IModelListener):
    """Bounded coalescer for summary-only SSE; never retains file records."""
    _MAX_SCOPES = 128

    def __init__(self):
        self.__scopes: OrderedDict[str, int] = OrderedDict()
        self.__reset = False
        self.__closed = False
        self.__lock = Lock()

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

    def take_next_event(self) -> Optional[dict[str, object]]:
        with self.__lock:
            if self.__closed:
                return None
            if self.__reset:
                self.__reset = False
                return {"event": "model-summary-reset", "reason": "coalesced"}
            if not self.__scopes:
                return None
            scopes = list(self.__scopes)
            self.__scopes.clear()
            return {"event": "model-summary-update", "path_pair_ids": scopes}

    def close(self) -> None:
        with self.__lock:
            self.__closed = True
            self.__scopes.clear()


class ModelApiHandler(IHandler):
    """Routes for paged model reads and scoped, lightweight invalidations."""

    _DEFAULT_LIMIT = 100
    _MAX_LIMIT = 200
    _MAX_CURSOR_LENGTH = 4096
    _KEEPALIVE_INTERVAL_SECONDS = 5.0
    _SUMMARY_MIN_INTERVAL_SECONDS = 0.5

    def __init__(self, controller: Controller, performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None):
        self.__controller = controller
        self.__performance_diagnostics = performance_diagnostics

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

    @staticmethod
    def __json_response(payload: object, status: int = 200) -> HTTPResponse:
        return HTTPResponse(
            body=json.dumps(payload), status=status,
            headers={"Content-Type": "application/json"},
        )

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
    def __public_page(cls, page: dict[str, object], query_signature: str) -> dict[str, object]:
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
        return page

    def __get_page(
        self, scope_id: str, parent_file_id: Optional[str], *, add_listener: Optional[ScopedModelListener] = None
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
        return self.__public_page(page, query_signature)

    def __handle_summary(self) -> HTTPResponse:
        if self.__performance_diagnostics is not None:
            self.__performance_diagnostics.increment("model_summary_serializations")
        return self.__json_response(self.__controller.get_model_summary())

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
                yield self.__sse("model-summary", summary, version if isinstance(version, int) else None)
                if reconnect_id:
                    # Replay is unavailable, but the just-emitted snapshot is
                    # already the complete normalized recovery payload.
                    yield self.__sse("model-summary", summary, version if isinstance(version, int) else None)
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
                        yield self.__sse("model-summary", snapshot,
                                         update_version if isinstance(update_version, int) else None)
                        pending_event = None
                        last_summary_at = time.monotonic()
                        last_keepalive_at = time.monotonic()
                    elif time.monotonic() - last_keepalive_at >= self._KEEPALIVE_INTERVAL_SECONDS:
                        yield ": keepalive\n\n"
                        last_keepalive_at = time.monotonic()
                    else:
                        time.sleep(0.1)
            finally:
                listener.close()
                self.__controller.remove_model_listener(listener)
        return stream()

    def __handle_roots(self, path_pair_id: str) -> HTTPResponse:
        scope_id = self.__validate_scope_id(path_pair_id)
        page = self.__get_page(scope_id, None)
        if self.__performance_diagnostics is not None:
            self.__performance_diagnostics.increment("model_page_serializations")
        return self.__json_response(
            page, 409 if page.get("error") == "cursor_reset_required" else 400 if page.get("error") else 200
        )

    def __handle_children(self, path_pair_id: str) -> HTTPResponse:
        scope_id = self.__validate_scope_id(path_pair_id)
        parent_file_id = self.__read_parent_file_id()
        page = self.__get_page(scope_id, parent_file_id)
        if self.__performance_diagnostics is not None:
            self.__performance_diagnostics.increment("model_page_serializations")
        return self.__json_response(
            page, 409 if page.get("error") == "cursor_reset_required" else 400 if page.get("error") else 200
        )

    @staticmethod
    def __sse(event: str, payload: dict[str, object], version: Optional[int] = None) -> str:
        event_id = "id: {}\n".format(version) if version is not None else ""
        return "{}event: {}\ndata: {}\n\n".format(
            event_id, event, json.dumps(payload, separators=(",", ":"))
        )

    def __handle_stream(self, path_pair_id: str) -> Iterator[str]:
        scope_id = self.__validate_scope_id(path_pair_id)
        listener = ScopedModelListener(scope_id)
        page = self.__get_page(scope_id, None, add_listener=listener)
        if page.get("error"):
            listener.close()
            return self.__json_response(page, 409 if page.get("error") == "cursor_reset_required" else 400)
        reconnect_id = bottle.request.get_header("Last-Event-ID", "").strip()
        bottle.response.content_type = "text/event-stream"
        bottle.response.cache_control = "no-cache"

        def stream() -> Iterator[str]:
            try:
                version = page.get("model_version")
                last_keepalive_at = time.monotonic()
                yield self.__sse("model-page", page, version if isinstance(version, int) else None)
                if reconnect_id:
                    yield self.__sse(
                        "model-reset",
                        {"model_version": version, "reason": "replay_unavailable"},
                        version if isinstance(version, int) else None,
                    )
                while True:
                    event = listener.take_next_event()
                    if event is not None:
                        event_name = event.pop("event")
                        if event_name == "model-invalidate":
                            changed_ids = event.get("file_ids")
                            updates = self.__controller.get_model_root_updates(
                                scope_id,
                                changed_ids if isinstance(changed_ids, list) else [],
                            )
                            event["records"] = updates["records"]
                            event["removed_file_ids"] = updates["removed_file_ids"]
                            event["model_version"] = updates["model_version"]
                        event_version = event.get("model_version")
                        yield self.__sse(
                            event_name if isinstance(event_name, str) else "model-reset",
                            event,
                            event_version if isinstance(event_version, int) else None,
                        )
                        last_keepalive_at = time.monotonic()
                    elif time.monotonic() - last_keepalive_at >= self._KEEPALIVE_INTERVAL_SECONDS:
                        # Comments are ignored by EventSource clients but keep
                        # idle proxies from discarding an otherwise healthy
                        # scoped subscription.
                        yield ": keepalive\n\n"
                        last_keepalive_at = time.monotonic()
                    else:
                        time.sleep(0.1)
            finally:
                listener.close()
                self.__controller.remove_model_listener(listener)

        return stream()
