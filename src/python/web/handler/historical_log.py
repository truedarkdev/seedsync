import base64
import io
import json
import logging
import math
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import NotRequired, TypeGuard, TypedDict

import bottle
from bottle import HTTPResponse

from common.config import Config
from common.redaction import redact_sensitive_text
from ..web_app import IHandler, WebApp


class HistoryRecord(TypedDict):
    id: str
    timestamp: str
    epoch: int | float
    level: str
    level_number: int
    logger: str
    message: str | None
    exception: str | None
    truncated: NotRequired[bool]


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_valid_epoch(value: object) -> TypeGuard[int | float]:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        try:
            datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return False
        return True
    return isinstance(value, float) and math.isfinite(value)


def _query_value(name: str, default: str | None = None) -> str | None:
    value = bottle.request.query.get(name, default)
    return value if isinstance(value, str) else default


HISTORY_SCHEMA = "seedsync.log-history.v1"
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500
MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_RANGE_SECONDS = 31 * 24 * 60 * 60
MAX_RECORD_TEXT_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
_ABSOLUTE_PATH_REGEX = re.compile(
    r"(?<![:\w/])(?:[A-Za-z]:\\[^\s'\"<>|]+|\\\\[^\s\\/]+\\[^\s'\"<>|]+|/(?!/)[^\s'\"<>]+)",
    re.IGNORECASE
)
CANONICAL_CONFIG_SENSITIVE_FIELDS = frozenset(
    key for fields in Config.SENSITIVE_FIELDS.values() for key in fields
)
_HISTORY_SENSITIVE_FIELDS = CANONICAL_CONFIG_SENSITIVE_FIELDS | {
    "password", "passwd", "api_key", "api-key", "token", "secret", "credential", "authorization"
}
_CANONICAL_SENSITIVE_KEY_PATTERN = "(?:{})".format(
    "|".join(re.escape(key) for key in sorted(_HISTORY_SENSITIVE_FIELDS, key=len, reverse=True))
)
_QUOTED_SECRET_REGEXES = (
    re.compile(
        rf'(?P<prefix>"{_CANONICAL_SENSITIVE_KEY_PATTERN}"\s*[:=]\s*")(?:\\.|[^"\\\r\n])*(?P<suffix>")',
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<prefix>'{_CANONICAL_SENSITIVE_KEY_PATTERN}'\s*[:=]\s*')(?:\\.|[^'\\\r\n])*(?P<suffix>')",
        re.IGNORECASE,
    ),
)
_UNQUOTED_CONFIG_SECRET_REGEX = re.compile(
    rf"(?P<prefix>(?<!['\"])\b{_CANONICAL_SENSITIVE_KEY_PATTERN}\s*[:=]\s*).*?"
    r"(?=(?:\s*[,;]\s*[A-Za-z_][\w.-]*\s*[:=])|\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_AUTHORIZATION_REGEX = re.compile(
    r"(?P<prefix>(?<!['\"])\bAuthorization\s*[:=]\s*)[^\r\n]+",
    re.IGNORECASE
)
_COLON_PATH_REGEX = re.compile(
    r"(?P<prefix>\b(?:file://|[A-Za-z][\w+.-]*:))(?P<path>/(?!/)[^\s'\"<>]+)",
    re.IGNORECASE
)
_CREDENTIAL_URL_REGEX = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^\s/@:]+(?::[^\s/@]*)?@[^\s/]+",
    re.IGNORECASE
)


def sanitize_log_text(value: object) -> str | None:
    if value is None:
        return None
    redacted = value if isinstance(value, str) else str(value)
    for pattern in _QUOTED_SECRET_REGEXES:
        redacted = pattern.sub(r"\g<prefix>**REDACTED**\g<suffix>", redacted)
    redacted = _UNQUOTED_CONFIG_SECRET_REGEX.sub(r"\g<prefix>**REDACTED**", redacted)
    redacted = _AUTHORIZATION_REGEX.sub(r"\g<prefix>**REDACTED**", redacted)
    redacted = _CREDENTIAL_URL_REGEX.sub(r"\g<scheme>**REDACTED**@**REDACTED**", redacted)
    redacted = _COLON_PATH_REGEX.sub(r"\g<prefix>**REDACTED_PATH**", redacted)
    redacted = redact_sensitive_text(redacted)
    if redacted is None:
        return None
    return _ABSOLUTE_PATH_REGEX.sub("**REDACTED_PATH**", redacted)


def _truncate_text(value: str | None, maximum: int = MAX_RECORD_TEXT_BYTES) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value, False
    return encoded[:maximum].decode("utf-8", errors="ignore") + "...[TRUNCATED]", True


def _sanitize_record_text(item: HistoryRecord) -> HistoryRecord:
    truncated = bool(item.get("truncated", False))
    logger_value, logger_truncated = _truncate_text(sanitize_log_text(item.get("logger")))
    item["logger"] = logger_value or ""
    truncated = truncated or logger_truncated
    for key in ("message", "exception"):
        value, field_truncated = _truncate_text(sanitize_log_text(item.get(key)))
        item[key] = value
        truncated = truncated or field_truncated
    item["truncated"] = truncated
    return item


def _record_id(record: logging.LogRecord) -> str:
    value = getattr(record, "seedsync_record_id", None)
    if not value:
        value = uuid.uuid4().hex
        record.seedsync_record_id = value
    return value


class HistoricalJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        exception = None
        if record.exc_info:
            exception = self.formatException(record.exc_info)
        elif record.exc_text:
            exception = record.exc_text
        item: HistoryRecord = {
            "id": _record_id(record),
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "epoch": record.created,
            "level": record.levelname,
            "level_number": record.levelno,
            "logger": record.name,
            "message": record.getMessage(),
            "exception": exception,
            "truncated": False,
        }
        return json.dumps(_sanitize_record_text(item), separators=(",", ":"))


def _validate_owned_object(path: str, expected_mode: int) -> None:
    details = os.lstat(path)
    expected_type = stat.S_IFDIR if expected_mode == 0o700 else stat.S_IFREG
    if stat.S_IFMT(details.st_mode) != expected_type:
        raise OSError("Unsafe historical log path type")
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and details.st_uid != getuid():
        raise OSError("Historical log path is not owned by the current user")
    os.chmod(path, expected_mode)


def _prepare_history_path(path: str, backup_count: int) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if os.path.lexists(directory):
        _validate_owned_object(directory, 0o700)
    else:
        os.mkdir(directory, 0o700)
        _validate_owned_object(directory, 0o700)
    for candidate in [path] + ["{}.{}".format(path, index) for index in range(1, backup_count + 1)]:
        if os.path.lexists(candidate):
            _validate_owned_object(candidate, 0o600)


class SecureRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename: str, maxBytes: int, backupCount: int) -> None:
        self._history_backup_count = backupCount
        _prepare_history_path(filename, backupCount)
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount,
                         encoding="utf-8", delay=True)
        self.stream = self._open()

    def _open(self) -> io.TextIOWrapper:
        _prepare_history_path(self.baseFilename, self._history_backup_count)
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise OSError("Historical log target is not a regular file")
            getuid = getattr(os, "getuid", None)
            if callable(getuid) and details.st_uid != getuid():
                raise OSError("Historical log target is not owned by the current user")
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, "a", encoding=self.encoding, errors=self.errors)
        except Exception:
            os.close(descriptor)
            raise

    def doRollover(self) -> None:
        super().doRollover()
        _prepare_history_path(self.baseFilename, self._history_backup_count)


def create_historical_log_handler(path: str, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    handler = SecureRotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count)
    handler.setFormatter(HistoricalJsonFormatter())
    return handler


class HistoricalLogStore:
    def __init__(self, path: str, backup_count: int) -> None:
        self.path = path
        self.backup_count = backup_count

    def query(
        self,
        *,
        start: float | None = None,
        end: float | None = None,
        levels: set[str] | None = None,
        logger: str | None = None,
        text: str | None = None,
        direction: str = "desc",
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, object]:
        records, scanned, malformed, truncated = self._read_records()
        records.sort(key=lambda item: (item["epoch"], item["id"]), reverse=direction == "desc")
        if start is not None:
            records = [item for item in records if item["epoch"] >= start]
        if end is not None:
            records = [item for item in records if item["epoch"] <= end]
        if levels:
            records = [item for item in records if item["level"] in levels]
        if logger:
            records = [item for item in records if item["logger"] == logger]
        if text:
            needle = text.lower()
            records = [item for item in records if needle in " ".join((
                item["logger"], item["message"] or "", item["exception"] or ""
            )).lower()]

        offset = 0
        if cursor:
            cursor_id = self._decode_cursor(cursor)
            indexes = [index for index, item in enumerate(records) if item["id"] == cursor_id]
            if not indexes:
                raise ValueError("invalid cursor")
            offset = indexes[0] + 1
        candidates = records[offset:offset + limit]
        page: list[HistoryRecord] = []
        output_bytes = 0
        output_truncated = False
        for item in candidates:
            item_bytes = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
            if page and output_bytes + item_bytes > MAX_RESPONSE_BYTES - 4096:
                output_truncated = True
                break
            page.append(item)
            output_bytes += item_bytes
        has_more = offset + len(page) < len(records)
        next_cursor = self._encode_cursor(page[-1]["id"]) if has_more and page else None
        return {
            "schema": HISTORY_SCHEMA,
            "records": page,
            "page": {"limit": limit, "direction": direction, "next_cursor": next_cursor,
                     "has_more": has_more},
            "evidence": {"scanned_bytes": scanned, "malformed_records_skipped": malformed,
                         "scan_truncated": truncated, "retention_files": self.backup_count + 1,
                         "max_scan_bytes": MAX_SCAN_BYTES, "output_bytes": output_bytes,
                         "max_response_bytes": MAX_RESPONSE_BYTES,
                         "max_record_text_bytes": MAX_RECORD_TEXT_BYTES,
                         "output_truncated": output_truncated},
        }

    def _read_records(self) -> tuple[list[HistoryRecord], int, int, bool]:
        records: list[HistoryRecord] = []
        scanned = 0
        malformed = 0
        truncated = False
        # Prefer the newest evidence when the bounded scan cannot cover all retained files.
        paths = [self.path] + ["{}.{}".format(self.path, index) for index in range(1, self.backup_count + 1)]
        for path in paths:
            if not os.path.lexists(path):
                continue
            try:
                details = os.lstat(path)
                if not stat.S_ISREG(details.st_mode):
                    raise OSError("Unsafe historical log read path")
                getuid = getattr(os, "getuid", None)
                if callable(getuid) and details.st_uid != getuid():
                    raise OSError("Historical log read path is not owned by the current user")
                flags = os.O_RDONLY
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                with os.fdopen(descriptor, "rb") as source:
                    remaining = MAX_SCAN_BYTES - scanned
                    file_size = os.fstat(source.fileno()).st_size
                    start_offset = max(0, file_size - remaining)
                    source.seek(start_offset)
                    data = source.read(remaining)
                    scanned += len(data)
                    lines = data.splitlines()
                    if start_offset > 0 and lines:
                        lines = lines[1:]
                        truncated = True
                    for raw_line in lines:
                        try:
                            item: object = json.loads(raw_line.decode("utf-8"))
                            if not self._valid_record(item):
                                raise ValueError()
                            records.append(_sanitize_record_text(item))
                        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
                            malformed += 1
                    if scanned >= MAX_SCAN_BYTES:
                        truncated = True
                        return records, scanned, malformed, truncated
            except OSError:
                logging.getLogger(__name__).warning("Historical log file could not be read", exc_info=True)
        return records, scanned, malformed, truncated

    @staticmethod
    def _valid_record(item: object) -> TypeGuard[HistoryRecord]:
        if not _is_object_dict(item):
            return False
        record_id = item.get("id")
        epoch = item.get("epoch")
        level_number = item.get("level_number")
        truncated = item.get("truncated", False)
        return (isinstance(record_id, str) and
                0 < len(record_id) <= 128 and re.fullmatch(r"[A-Za-z0-9._-]+", record_id) is not None and
                isinstance(item.get("timestamp"), str) and
                _is_valid_epoch(epoch) and
                isinstance(item.get("level"), str) and
                isinstance(level_number, int) and not isinstance(level_number, bool) and
                isinstance(item.get("logger"), str) and isinstance(item.get("message"), (str, type(None))) and
                isinstance(item.get("exception"), (str, type(None))) and type(truncated) is bool)

    @staticmethod
    def _encode_cursor(record_id: str) -> str:
        payload = json.dumps({"v": 1, "id": record_id}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: object) -> str:
        if not isinstance(cursor, str) or len(cursor) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError("invalid cursor")
        try:
            payload: object = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if not _is_object_dict(payload) or payload.get("v") != 1 or not isinstance(payload.get("id"), str):
                raise ValueError()
            record_id = payload["id"]
            if not isinstance(record_id, str):
                raise ValueError()
            return record_id
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("invalid cursor")


class HistoricalLogHandler(IHandler):
    _LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    def __init__(self, store: HistoricalLogStore, logger: logging.Logger) -> None:
        self.store = store
        self.logger = logger

    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_handler("/server/logs/history/v1", self._get, required_scope="admin")

    def _get(self) -> HTTPResponse:
        try:
            limit = self._integer("limit", DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
            start = self._timestamp("start")
            end = self._timestamp("end")
            if start is not None and end is not None and (end < start or end - start > MAX_RANGE_SECONDS):
                raise ValueError("invalid time range")
            direction = (_query_value("direction", "desc") or "desc").lower()
            if direction not in {"asc", "desc"}:
                raise ValueError("invalid direction")
            raw_levels = _query_value("level")
            levels = {value.strip().upper() for value in raw_levels.split(",")} if raw_levels else None
            if levels and not levels.issubset(self._LEVELS):
                raise ValueError("invalid level")
            logger = self._bounded("logger", 200)
            text = self._bounded("text", 500)
            payload = self.store.query(start=start, end=end, levels=levels, logger=logger, text=text,
                                       direction=direction, limit=limit,
                                       cursor=_query_value("cursor"))
            return HTTPResponse(body=json.dumps(payload), content_type="application/json")
        except ValueError as exc:
            self.logger.warning("Rejected historical log query: %s", exc)
            return HTTPResponse(body=json.dumps({"error": "invalid log history query"}), status=400,
                                content_type="application/json")

    @staticmethod
    def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = _query_value(name)
        try:
            value = default if not raw else int(raw)
        except ValueError:
            raise ValueError("invalid {}".format(name))
        if value < minimum or value > maximum:
            raise ValueError("invalid {}".format(name))
        return value

    @staticmethod
    def _timestamp(name: str) -> float | None:
        raw = _query_value(name)
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            raise ValueError("invalid {}".format(name))
        if value < 0:
            raise ValueError("invalid {}".format(name))
        return value

    @staticmethod
    def _bounded(name: str, maximum: int) -> str | None:
        value = _query_value(name)
        if value is not None and len(value) > maximum:
            raise ValueError("invalid {}".format(name))
        return value or None
