# Copyright 2026, SeedSync Contributors, All rights reserved.

import copy
import json
import multiprocessing
import queue
import re
import time
from collections import deque
from threading import Lock
from typing import Any, Callable, Deque, Dict, List, Optional


class BreadcrumbTraceEmitter:
    def __init__(self, record_queue: multiprocessing.Queue):
        self.__record_queue = record_queue

    def record(self, source: str, message: str, details: Optional[Dict[str, Any]] = None, **metadata):
        created_ns = time.time_ns()
        self.__record_queue.put({
            "source": source,
            "message": message,
            "details": details,
            "metadata": metadata,
            "created_ns": created_ns,
            "created_ms": int(created_ns / 1_000_000),
        })


class BreadcrumbTraceNoopEmitter:
    def record(self, source: str, message: str, details: Optional[Dict[str, Any]] = None, **metadata):
        pass


class BreadcrumbTraceCollector:
    """
    Small bounded in-memory breadcrumb collector.

    The collector is intentionally lightweight and opt-in. Callers can keep
    recording breadcrumbs without having to guard every call site; the
    collector checks the enabled flag on each write and simply no-ops when the
    facility is disabled.
    """

    __MAX_DETAIL_STRING_LENGTH = 256
    __MAX_COLLECTION_DEPTH = 3
    __MAX_LIST_ITEMS = 16
    __SENSITIVE_KEYWORDS = (
        "password",
        "passwd",
        "secret",
        "token",
        "credential",
        "authorization",
        "auth",
        "cookie",
        "session",
        "api_key",
        "apikey",
    )
    __COMMAND_KEYWORDS = (
        "command",
        "cmd",
        "argv",
        "args",
        "script",
        "shell",
    )
    __COMMAND_VALUE_PATTERNS = (
        (re.compile(r'(-u\s+\S+,)\S+', re.IGNORECASE), r'\1**REDACTED**'),
        (re.compile(r'((?:remote_)?password\s*[=:]\s*)\S+', re.IGNORECASE), r'\1**REDACTED**'),
        (re.compile(r'((?:api[_-]?key|token|secret|credential|authorization|passwd)\s*[=:]\s*)\S+', re.IGNORECASE), r'\1**REDACTED**'),
        (re.compile(r'sftp://\S+@\S+', re.IGNORECASE), 'sftp://**REDACTED**@**REDACTED**'),
        (re.compile(
            r'(^|[\s\'"\[])([a-zA-Z0-9_][\w.\-]*)@('
            r'(?:\d{1,3}(?:\.\d{1,3}){3})|'
            r'(?:[a-zA-Z0-9_][a-zA-Z0-9_-]*)|'
            r'(?:[a-zA-Z0-9][a-zA-Z0-9-]*(?:\.[a-zA-Z0-9-]+)+)'
            r')(?=[\s\'"\],:/>])',
            re.MULTILINE
        ), r'\1**REDACTED**@**REDACTED**'),
    )

    def __init__(self, enabled_getter: Callable[[], bool], max_entries: int = 128):
        if max_entries < 1:
            raise ValueError("max_entries must be greater than 0")
        self.__enabled_getter = enabled_getter
        self.__max_entries = max_entries
        self.__entries: Deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self.__external_records = None
        self.__external_records_lock = Lock()
        self.__lock = Lock()
        self.__version = 0
        self.__last_reset_version = 0
        self.__window_reset_pending = False
        self.__window_truncated_pending = False
        self.__last_signature = None
        self.__last_failure_entry = None
        self.__last_failure_version = None

    def create_emitter(self) -> BreadcrumbTraceEmitter:
        if not self.is_enabled():
            return BreadcrumbTraceNoopEmitter()
        with self.__external_records_lock:
            if self.__external_records is None:
                self.__external_records = multiprocessing.Queue()
            return BreadcrumbTraceEmitter(self.__external_records)

    def is_enabled(self) -> bool:
        try:
            return bool(self.__enabled_getter())
        except Exception:
            return False

    @property
    def max_entries(self) -> int:
        return self.__max_entries

    @property
    def version(self) -> int:
        return self.__version

    def record(self, source: str, message: str, details: Optional[Dict[str, Any]] = None, **metadata):
        self.__drain_external_records()
        self.__record_entry(source, message, details, **metadata)

    def __record_entry(self, source: str, message: str, details: Optional[Dict[str, Any]] = None, **metadata):
        if not self.is_enabled():
            return

        created_ns = metadata.pop("created_ns", None)
        created_ms = metadata.pop("created_ms", None)
        if created_ns is None:
            created_ns = time.time_ns()
        if created_ms is None:
            created_ms = int(created_ns / 1_000_000)

        stage = metadata.pop("stage", None)
        event_type = metadata.pop("event_type", "breadcrumb")
        corr_id = metadata.pop("corr_id", None)
        flow_id = metadata.pop("flow_id", None)
        file_id = metadata.pop("file_id", None)
        path_pair_id = metadata.pop("path_pair_id", None)
        path_pair_name = metadata.pop("path_pair_name", None)
        trace_scope = metadata.pop("trace_scope", "flow")
        if metadata:
            extra_details = dict(metadata)
            if details is None:
                details = extra_details
            elif isinstance(details, dict):
                details = {**details, **extra_details}
            else:
                details = {
                    "value": details,
                    **extra_details,
                }

        sanitized_details = self.__sanitize_value(details, key=None, depth=0)
        if sanitized_details is None:
            sanitized_details = {}

        entry = {
            "created_ms": created_ms,
            "created_ns": created_ns,
            "source": self.__truncate_string(source),
            "stage": self.__truncate_string(stage if stage is not None else message),
            "event_type": self.__truncate_string(event_type),
            "corr_id": self.__truncate_string(corr_id),
            "flow_id": self.__truncate_string(flow_id),
            "file_id": self.__truncate_string(file_id),
            "path_pair_id": self.__truncate_string(path_pair_id),
            "path_pair_name": self.__truncate_string(path_pair_name),
            "trace_scope": self.__truncate_string(trace_scope),
            "message": self.__truncate_string(message),
            "details": sanitized_details,
        }

        with self.__lock:
            signature = self.__signature(entry)
            if self.__entries and signature == self.__last_signature:
                last_entry = self.__entries[-1]
                last_entry["repeat_count"] = last_entry.get("repeat_count", 1) + 1
                last_entry["last_seen_ms"] = created_ms
                last_entry["last_seen_ns"] = created_ns
                self.__version += 1
                last_entry["last_seen_version"] = self.__version
                if event_type == "failure":
                    self.__last_failure_entry = copy.deepcopy(last_entry)
                    self.__last_failure_version = self.__version
                return

            self.__version += 1
            entry["version"] = self.__version
            entry["repeat_count"] = 1
            entry["last_seen_ms"] = created_ms
            entry["last_seen_ns"] = created_ns
            entry["last_seen_version"] = self.__version
            if len(self.__entries) == self.__max_entries:
                self.__window_truncated_pending = True
            self.__entries.append(entry)
            self.__last_signature = signature
            if event_type == "failure":
                self.__last_failure_entry = copy.deepcopy(entry)
                self.__last_failure_version = self.__version

    def snapshot(self, since_version: Optional[int] = None) -> Dict[str, Any]:
        self.__drain_external_records()
        with self.__lock:
            enabled = self.is_enabled()
            if not enabled:
                window_reset = bool(self.__entries)
                if window_reset:
                    self.__entries.clear()
                    self.__last_signature = None
                    self.__last_failure_entry = None
                    self.__last_failure_version = None
                    self.__last_reset_version = self.__version
                    self.__window_reset_pending = True
                payload = self.__build_snapshot_payload(
                    entries=[],
                    all_entries=[],
                    since_version=since_version,
                    window_reset=window_reset,
                    window_reset_reason="disabled" if window_reset else None,
                    window_truncated=self.__window_truncated_pending,
                )
                self.__window_reset_pending = False
                self.__window_truncated_pending = False
                return payload

            all_entries = list(self.__entries)
            entries = list(self.__entries)
            window_reset = self.__window_reset_pending
            if since_version is not None:
                window_reset = window_reset or since_version < self.__last_reset_version
                entries = [entry for entry in entries if entry["version"] > since_version]
            entries = copy.deepcopy(entries)
            payload = self.__build_snapshot_payload(
                entries=entries,
                all_entries=all_entries,
                since_version=since_version,
                window_reset=window_reset,
                window_reset_reason="reset" if window_reset else None,
                window_truncated=self.__window_truncated_pending,
            )
            self.__window_reset_pending = False
            self.__window_truncated_pending = False
            return payload

    def __build_snapshot_payload(
        self,
        entries: List[Dict[str, Any]],
        all_entries: List[Dict[str, Any]],
        since_version: Optional[int],
        window_reset: bool,
        window_reset_reason: Optional[str],
        window_truncated: bool,
    ) -> Dict[str, Any]:
        payload = {
            "enabled": self.is_enabled(),
            "version": self.__version,
            "since_version": since_version,
            "last_reset_version": self.__last_reset_version,
            "window_reset": window_reset,
            "window_reset_reason": window_reset_reason,
            "window_truncated": window_truncated,
            "max_entries": self.__max_entries,
            "entry_count": len(entries),
            "latest_failure_version": self.__last_failure_version,
            "latest_failure_entry": copy.deepcopy(self.__last_failure_entry),
            "failure_summary": self.__build_failure_summary(all_entries),
            "entries": entries,
        }
        return payload

    def __build_failure_summary(self, entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if self.__last_failure_entry is None:
            return None

        failure_entry = copy.deepcopy(self.__last_failure_entry)
        failure_version = failure_entry.get("version")
        corr_id = failure_entry.get("corr_id")
        trace_scope = failure_entry.get("trace_scope") or "flow"

        recent_entries = []
        for entry in entries:
            if failure_version is not None and entry["version"] > failure_version:
                continue
            if trace_scope == "aggregate":
                if entry.get("trace_scope") != "aggregate":
                    continue
            else:
                if corr_id is not None and entry.get("corr_id") != corr_id:
                    continue
                if corr_id is None:
                    failure_file_id = failure_entry.get("file_id")
                    failure_path_pair_id = failure_entry.get("path_pair_id")
                    if failure_path_pair_id is not None:
                        if entry.get("path_pair_id") != failure_path_pair_id:
                            continue
                    elif failure_file_id is not None:
                        if entry.get("file_id") != failure_file_id:
                            continue
                    else:
                        continue
            recent_entries.append(entry)

        if not recent_entries:
            recent_entries = [failure_entry]

        recent_stage_trail = []
        for entry in recent_entries[-5:]:
            recent_stage_trail.append({
                "version": entry["version"],
                "corr_id": entry["corr_id"],
                "stage": entry["stage"],
                "message": entry["message"],
                "event_type": entry["event_type"],
                "trace_scope": entry["trace_scope"],
                "file_id": entry["file_id"],
                "path_pair_id": entry["path_pair_id"],
                "path_pair_name": entry["path_pair_name"],
            })

        return {
            "corr_id": failure_entry["corr_id"],
            "stage": failure_entry["stage"],
            "message": failure_entry["message"],
            "version": failure_entry["version"],
            "event_type": failure_entry["event_type"],
            "file_id": failure_entry["file_id"],
            "path_pair_id": failure_entry["path_pair_id"],
            "path_pair_name": failure_entry["path_pair_name"],
            "created_ms": failure_entry["created_ms"],
            "created_ns": failure_entry["created_ns"],
            "repeat_count": failure_entry["repeat_count"],
            "trace_scope": trace_scope,
            "recent_stage_trail": recent_stage_trail,
            "window_entry_count": len(recent_entries),
        }

    def __signature(self, entry: Dict[str, Any]) -> str:
        signature_payload = {
            "source": entry["source"],
            "stage": entry["stage"],
            "event_type": entry["event_type"],
            "corr_id": entry["corr_id"],
            "flow_id": entry["flow_id"],
            "file_id": entry["file_id"],
            "path_pair_id": entry["path_pair_id"],
            "path_pair_name": entry["path_pair_name"],
            "trace_scope": entry["trace_scope"],
            "message": entry["message"],
            "details": entry["details"],
        }
        return json.dumps(signature_payload, sort_keys=True, default=str)

    def __sanitize_value(self, value: Any, key: Optional[str], depth: int) -> Any:
        if value is None:
            return None
        if key is not None and self.__is_sensitive_key(key):
            return "<redacted>"
        if depth >= self.__MAX_COLLECTION_DEPTH:
            return self.__truncate_string(self.__sanitize_string_content(str(value)))
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if key is not None and self.__is_command_key(key):
                return "<redacted>"
            return self.__truncate_string(self.__sanitize_string_content(value))
        if isinstance(value, dict):
            sanitized = {}
            for item_key, item_value in value.items():
                item_key_str = str(item_key)
                if self.__is_sensitive_key(item_key_str):
                    sanitized[item_key_str] = "<redacted>"
                else:
                    sanitized[item_key_str] = self.__sanitize_value(
                        item_value,
                        item_key_str,
                        depth + 1
                    )
            return sanitized
        if isinstance(value, (list, tuple, set)):
            if key is not None and self.__is_command_key(key):
                return "<redacted>"
            sanitized_list = []
            for index, item in enumerate(list(value)):
                if index >= self.__MAX_LIST_ITEMS:
                    sanitized_list.append("<truncated>")
                    break
                sanitized_list.append(self.__sanitize_value(item, key, depth + 1))
            return sanitized_list
        return self.__truncate_string(self.__sanitize_string_content(str(value)))

    def __is_sensitive_key(self, key: str) -> bool:
        lowered = key.lower()
        return any(keyword in lowered for keyword in BreadcrumbTraceCollector.__SENSITIVE_KEYWORDS)

    def __is_command_key(self, key: str) -> bool:
        lowered = key.lower()
        return any(keyword in lowered for keyword in BreadcrumbTraceCollector.__COMMAND_KEYWORDS)

    def __truncate_string(self, value: Any) -> Any:
        if value is None:
            return None
        value = str(value)
        if len(value) <= BreadcrumbTraceCollector.__MAX_DETAIL_STRING_LENGTH:
            return value
        return value[:BreadcrumbTraceCollector.__MAX_DETAIL_STRING_LENGTH] + "...<truncated>"

    def __sanitize_string_content(self, value: str) -> str:
        if value is None:
            return None

        sanitized = value
        for pattern, replacement in BreadcrumbTraceCollector.__COMMAND_VALUE_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    def __drain_external_records(self):
        if self.__external_records is None:
            return
        while True:
            try:
                external_record = self.__external_records.get_nowait()
            except queue.Empty:
                return
            metadata = external_record.get("metadata", {})
            self.__record_entry(
                external_record.get("source"),
                external_record.get("message"),
                external_record.get("details"),
                stage=metadata.get("stage"),
                event_type=metadata.get("event_type", "breadcrumb"),
                corr_id=metadata.get("corr_id"),
                flow_id=metadata.get("flow_id"),
                file_id=metadata.get("file_id"),
                path_pair_id=metadata.get("path_pair_id"),
                path_pair_name=metadata.get("path_pair_name"),
                created_ns=external_record.get("created_ns"),
                created_ms=external_record.get("created_ms"),
            )
