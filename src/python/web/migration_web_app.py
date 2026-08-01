"""Restricted web runtime used while a selected configuration migration blocks startup."""

from __future__ import annotations

import json
import ipaddress
import logging
import re
import secrets
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit

import bottle
from bottle import static_file

from migration import MigrationCoordinator, MigrationDecision, MigrationState
from .web_app_job import MyWSGIRefServer


RELEASE_TOUR_FEATURES = (
    {
        "key": "path-pairs",
        "title": "Sync more than one folder",
        "summary": (
            "Add path pairs to keep separate remote and local folders—such as Movies and Series—"
            "running in the same SeedSync instance."
        ),
    },
    {
        "key": "accurate-progress",
        "title": "Progress you can trust",
        "summary": (
            "See live byte progress several times a second, with stop, resume, and final-move updates "
            "reconciled for a smoother view that stays closer to the real transfer."
        ),
    },
    {
        "key": "secure-access",
        "title": "Safer local access",
        "summary": (
            "Claim the first trusted browser, remember approved browser sessions, and create scoped API "
            "keys you can rotate or revoke."
        ),
    },
    {
        "key": "notifications",
        "title": "Notifications where you want them",
        "summary": (
            "Send selected download, extraction, and remote-delete events through a Generic Webhook or "
            "Apprise API, then check the connection with a test notification."
        ),
    },
    {
        "key": "transfer-choices",
        "title": "Choose your transfer engine",
        "summary": (
            "Use LFTP with SFTP or FTPS, or choose rclone with SFTP, while keeping the familiar SeedSync "
            "workflow."
        ),
    },
    {
        "key": "historical-logs",
        "title": "Find problems faster",
        "summary": (
            "Search retained logs by text, severity, logger, or time range, with bounded history and "
            "sensitive values redacted."
        ),
    },
)

_MAX_APPLY_BODY_BYTES = 1024
def _canonical_origin(value: str) -> tuple[str, str, int]:
    """Parse one exact HTTP(S) origin without accepting URL adornments."""
    if not isinstance(value, str) or not value or value != value.strip() or any(
        character.isspace() or character == "," for character in value
    ):
        raise ValueError("migration allowed origins must be individual canonical origins")
    separator = value.find("://")
    if separator <= 0 or any(character in value[separator + 3:] for character in "/?#"):
        raise ValueError("migration allowed origins must contain only scheme, host, and optional port")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("migration allowed origin has an invalid host") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("migration allowed origins must contain only scheme, host, and optional port")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("migration allowed origin has an invalid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("migration allowed origin has an invalid port")
    return parsed.scheme, parsed.hostname.casefold(), port


def normalize_migration_allowed_origin(value: str) -> str:
    """Argparse/environment validator for an exact migration origin."""
    _canonical_origin(value)
    return value


def validate_migration_allowed_origins(values: tuple[str, ...]) -> tuple[str, ...]:
    """Reject duplicate normalized authorities while preserving operator text."""
    canonical = [_canonical_origin(value) for value in values]
    if len(canonical) != len(set(canonical)):
        raise ValueError("migration allowed origins contain a duplicate normalized origin")
    return values


def _request_origin() -> tuple[str, str, int] | None:
    """Return the direct WSGI request authority; forwarded headers are ignored."""
    scheme = str(bottle.request.environ.get("wsgi.url_scheme", "")).casefold()
    host_header = bottle.request.environ.get("HTTP_HOST")
    if (
        scheme not in {"http", "https"}
        or not isinstance(host_header, str)
        or not host_header
        or host_header != host_header.strip()
        or any(character.isspace() or character in ",/@?#" for character in host_header)
    ):
        return None
    try:
        parsed = urlsplit("{}://{}".format(scheme, host_header))
    except ValueError:
        return None
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return scheme, parsed.hostname.casefold(), port


def _default_admits_hostname(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _safe_display_text(value: object, *, maximum_length: int) -> str:
    """Keep coordinator-owned display text bounded and free of control characters."""
    if not isinstance(value, str):
        return ""
    printable = "".join(character for character in value if character.isprintable())
    return printable[:maximum_length]


def _safe_diagnostic_field(value: object, *, maximum_length: int = 160) -> str:
    """Keep log metadata single-line and inert even for unusual exception types."""
    text = str(value)
    safe = "".join(
        character if character.isascii() and (character.isalnum() or character in "._:-") else "_"
        for character in text
    )[:maximum_length]
    return safe or "unknown"


def migration_status_payload(
    decision: MigrationDecision,
    *,
    csrf_token: str = "",
    operation_status: str = "idle",
    backup_ready: bool = False,
) -> dict[str, Any]:
    """Serialize the bounded migration-only capability and progress contract."""
    error: dict[str, str] | None = None
    if decision.state == MigrationState.FAILED:
        error = {
            "code": "migration_apply_failed" if decision.retryable else "migration_preflight_failed",
            "message": (
                "The migration attempt did not complete. The retained backup remains available."
                if decision.retryable
                else "SeedSync could not complete the migration readiness check."
            ),
        }

    operation_running = operation_status == "running" or decision.state == MigrationState.RUNNING
    has_selected_migration = bool(decision.migration_id and decision.source_schema)
    apply_available = (
        decision.state == MigrationState.REQUIRED
        and has_selected_migration
        and not operation_running
    )
    retry_available = (
        decision.state == MigrationState.FAILED
        and bool(decision.migration_id)
        and decision.retryable
        and not operation_running
    )
    continue_available = (
        decision.state == MigrationState.COMPLETE
        and not decision.normal_startup_released
        and not operation_running
    )
    if operation_running:
        blocker = "migration_running"
    elif decision.state == MigrationState.COMPLETE and not decision.normal_startup_released:
        blocker = "normal_startup_pending"
    elif decision.state == MigrationState.COMPLETE:
        blocker = None
    elif decision.state == MigrationState.FAILED and not decision.retryable:
        blocker = "migration_not_retryable"
    elif not (apply_available or retry_available):
        blocker = "migration_not_available"
    else:
        blocker = None

    normal_startup_released = (
        decision.normal_startup_released if decision.state == MigrationState.COMPLETE else False
    )
    return {
        "schema_version": 2,
        "mode": "migration_required",
        "state": decision.state.value,
        "migration_id": _safe_display_text(decision.migration_id, maximum_length=160) or None,
        "source_schema": _safe_display_text(decision.source_schema, maximum_length=80) or None,
        "target_schema": _safe_display_text(decision.target_schema, maximum_length=80) or None,
        "features": [dict(feature) for feature in RELEASE_TOUR_FEATURES],
        "error": error,
        "retryable": bool(decision.retryable),
        "capabilities": {
            "apply": apply_available,
            "retry": retry_available,
            "continue": continue_available,
            "restore": False,
        },
        "normal_startup": {
            "released": normal_startup_released,
            "requires_continue": decision.state == MigrationState.COMPLETE and not normal_startup_released,
        },
        "backup": {
            "required": True,
            "complete_restore_ready": backup_ready,
            "status": "ready" if backup_ready else "created_before_apply",
        },
        "operation": {
            "status": "running" if operation_running else operation_status,
            "message": {
                "idle": "Ready to create and validate the retained backup before migration.",
                "running": "Creating or validating the retained backup and applying the migration.",
                "succeeded": "Migration completed. Review the checkpoint, then continue to SeedSync when ready.",
                "failed": "Migration stopped safely. Review the status before retrying.",
            }.get("running" if operation_running else operation_status, "Migration status changed."),
        },
        "action": {
            "csrf_token": csrf_token,
            "confirmation": (
                "MIGRATE {}".format(decision.migration_id) if decision.migration_id else ""
            ),
        },
        "blocker": blocker,
    }


class MigrationWebApp(bottle.Bottle):
    """Minimal, sessionless application with no normal SeedSync handlers."""

    _CONTENT_SECURITY_POLICY = (
        "default-src 'self'; base-uri 'self'; connect-src 'self'; "
        "font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'self' data:; object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    )
    _STATIC_SUFFIXES = {".css", ".ico", ".js", ".png", ".svg", ".woff", ".woff2"}
    _MIGRATION_ASSETS = {
        "assets/logo.png",
        "assets/favicon.png",
        "assets/migration/current-settings.png",
        "assets/migration/first-claim.png",
        "assets/migration/historical-logs.png",
        "assets/migration/large-queues.png",
        "assets/migration/notifications.png",
        "assets/migration/path-pairs-overview.png",
        "assets/migration/path-pairs.png",
        "assets/migration/progress.png",
    }

    def __init__(
        self,
        html_path: str,
        coordinator: MigrationCoordinator,
        *,
        on_continue: Callable[[], None] | None = None,
        allowed_origins: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self._html_root = Path(html_path).resolve()
        self._coordinator = coordinator
        self._on_continue = on_continue or (lambda: None)
        self._csrf_token = secrets.token_urlsafe(32)
        validated_origins = validate_migration_allowed_origins(allowed_origins)
        self._allowed_origins = frozenset(_canonical_origin(value) for value in validated_origins)
        self._operation_lock = threading.Lock()
        self._execution = SimpleNamespace(status="idle", worker=None)

        self.hook("before_request")(self._admit_request_authority)
        self.hook("before_request")(self._deny_unregistered_server_routes)
        self.hook("after_request")(self._apply_security_headers)
        self.get("/")(self._redirect_to_migration)
        self.get("/bootstrap")(self._normal_startup_not_ready)
        self.get("/server/migration/v1/status")(self._status)
        self.post("/server/migration/v1/apply")(self._apply)
        self.post("/server/migration/v1/continue")(self._continue_normal_startup)
        self.get("/migration")(self._index)
        self.get("/migration/")(self._index)
        self.get("/migration/<spa_path:path>")(self._migration_fallback)
        self.get("/<file_path:path>")(self._static)

    @staticmethod
    def _apply_security_headers(response: bottle.BaseResponse | None = None) -> bottle.BaseResponse:
        target = response or bottle.response
        target.set_header("Cache-Control", "no-store, max-age=0")
        target.set_header("Pragma", "no-cache")
        target.set_header("Content-Security-Policy", MigrationWebApp._CONTENT_SECURITY_POLICY)
        target.set_header("X-Content-Type-Options", "nosniff")
        target.set_header("X-Frame-Options", "DENY")
        target.set_header("Referrer-Policy", "no-referrer")
        target.set_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return target

    @staticmethod
    def _deny_unregistered_server_routes() -> None:
        if not bottle.request.path.startswith("/server/"):
            return
        if (
            bottle.request.path not in {
                "/server/migration/v1/status", "/server/migration/v1/apply",
                "/server/migration/v1/continue",
            }
            or (
                bottle.request.path.endswith("/status")
                and bottle.request.method not in {"GET", "HEAD"}
            )
            or (
                bottle.request.path.endswith("/apply")
                and bottle.request.method != "POST"
            )
            or (
                bottle.request.path.endswith("/continue")
                and bottle.request.method != "POST"
            )
        ):
            bottle.abort(404)

    def _admit_request_authority(self) -> None:
        authority = _request_origin()
        if authority is None or not (
            _default_admits_hostname(authority[1]) or authority in self._allowed_origins
        ):
            bottle.abort(403)

    @staticmethod
    def _redirect_to_migration() -> bottle.HTTPResponse:
        return bottle.redirect("/migration")

    def _normal_startup_not_ready(self) -> bottle.HTTPResponse:
        """Keep the normal-runtime readiness probe distinct during migration."""
        return self._apply_security_headers(bottle.HTTPResponse(
            body='{"ready":false}', status=503,
            content_type="application/json; charset=utf-8",
        ))

    def _status(self) -> bottle.HTTPResponse:
        try:
            decision = self._coordinator.status()
        except Exception:
            # Keep this sessionless surface deterministic and non-leaky even if
            # the underlying on-disk state changes after startup.
            decision = MigrationDecision(
                state=MigrationState.FAILED,
                error="Migration readiness status is unavailable",
                retryable=False,
            )
        payload = self._payload(decision)
        return self._apply_security_headers(bottle.HTTPResponse(
            body=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            status=200,
            content_type="application/json; charset=utf-8",
        ))

    def _payload(self, decision: MigrationDecision) -> dict[str, Any]:
        with self._operation_lock:
            operation_status = self._execution.status
        if operation_status == "running":
            backup_ready = False
        else:
            try:
                backup_ready = self._coordinator.retained_backup_ready(decision)
            except Exception:
                backup_ready = False
        return migration_status_payload(
            decision,
            csrf_token=self._csrf_token,
            operation_status=operation_status,
            backup_ready=backup_ready,
        )

    def _same_origin_request(self) -> bool:
        origin = bottle.request.headers.get("Origin", "")
        try:
            parsed_origin = _canonical_origin(origin)
        except ValueError:
            return False
        request_origin = _request_origin()
        return bool(
            request_origin is not None
            and parsed_origin == request_origin
            and (
                _default_admits_hostname(request_origin[1])
                or request_origin in self._allowed_origins
            )
        )

    @staticmethod
    def _read_apply_body() -> dict[str, Any]:
        # The WSGI server exposes a socket-backed stream, so never read beyond
        # the declared frame. The bundled WSGIRef request handler uses HTTP/1.0,
        # so request connections close without an application hop-by-hop header.
        transfer_encoding = bottle.request.environ.get("HTTP_TRANSFER_ENCODING", "")
        if transfer_encoding:
            bottle.abort(400)
        raw_length = bottle.request.environ.get("CONTENT_LENGTH")
        if not isinstance(raw_length, str) or re.fullmatch(r"[1-9][0-9]*", raw_length) is None:
            bottle.abort(400)
        declared_length = int(raw_length)
        if declared_length > _MAX_APPLY_BODY_BYTES or bottle.request.content_type != "application/json":
            bottle.abort(400)
        raw_body = bottle.request.environ["wsgi.input"].read(declared_length)
        if len(raw_body) != declared_length:
            bottle.abort(400)
        try:
            text = raw_body.decode("utf-8")
            json_text = text.lstrip()
            decoder = json.JSONDecoder()
            body, end = decoder.raw_decode(json_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            bottle.abort(400)
        if json_text[end:].strip() or not isinstance(body, dict):
            bottle.abort(400)
        return body

    def _apply(self) -> bottle.HTTPResponse:
        # This protects the unauthenticated migration page from cross-origin
        # browser mutations. It does not authenticate another direct client on
        # the same trusted network that can first read the status token.
        supplied_token = bottle.request.headers.get("X-SeedSync-Migration-CSRF", "")
        if not self._same_origin_request() or not secrets.compare_digest(
            supplied_token, self._csrf_token,
        ):
            bottle.abort(403)
        body = self._read_apply_body()
        if not isinstance(body, dict) or set(body) != {"confirmation", "retry"}:
            bottle.abort(400)

        with self._operation_lock:
            if self._execution.worker is not None and self._execution.worker.is_alive():
                bottle.abort(409)
            try:
                decision = self._coordinator.status()
            except Exception:
                bottle.abort(409)
            retry = body.get("retry")
            expected_confirmation = (
                "MIGRATE {}".format(decision.migration_id) if decision.migration_id else ""
            )
            allowed = (
                decision.state == MigrationState.REQUIRED
                and bool(decision.migration_id and decision.source_schema)
                and retry is False
            ) or (
                decision.state == MigrationState.FAILED
                and bool(decision.migration_id)
                and decision.retryable
                and retry is True
            )
            if (
                not allowed
                or not isinstance(body.get("confirmation"), str)
                or not secrets.compare_digest(body["confirmation"], expected_confirmation)
            ):
                bottle.abort(409)
            self._execution.status = "running"
            worker = threading.Thread(
                target=self._run_apply,
                kwargs={"retry": bool(retry)},
                name="SeedSyncMigrationApply",
                daemon=True,
            )
            self._execution.worker = worker
            worker.start()

        return self._apply_security_headers(bottle.HTTPResponse(
            body=json.dumps(self._payload(decision), sort_keys=True, separators=(",", ":")),
            status=202,
            content_type="application/json; charset=utf-8",
        ))

    def _run_apply(self, *, retry: bool) -> None:
        succeeded = False
        try:
            decision = self._coordinator.apply_confirmed(retry=retry)
            succeeded = decision.state == MigrationState.COMPLETE
        except Exception as error:
            self._log_apply_failure(error)
        with self._operation_lock:
            self._execution.status = "succeeded" if succeeded else "failed"

    def _continue_normal_startup(self) -> bottle.HTTPResponse:
        """Release the completed checkpoint only after a protected user action."""
        supplied_token = bottle.request.headers.get("X-SeedSync-Migration-CSRF", "")
        if not self._same_origin_request() or not secrets.compare_digest(
            supplied_token, self._csrf_token,
        ):
            bottle.abort(403)
        body = self._read_apply_body()
        if body != {}:
            bottle.abort(400)
        with self._operation_lock:
            if self._execution.worker is not None and self._execution.worker.is_alive():
                bottle.abort(409)
            try:
                decision = self._coordinator.release_normal_startup()
            except Exception:
                bottle.abort(409)
            self._execution.status = "succeeded"
        response = self._apply_security_headers(bottle.HTTPResponse(
            body=json.dumps(self._payload(decision), sort_keys=True, separators=(",", ":")),
            status=202,
            content_type="application/json; charset=utf-8",
        ))
        self._on_continue()
        return response

    @staticmethod
    def _log_apply_failure(error: Exception) -> None:
        """Record a diagnosable background failure without logging request data.

        Exception text can include configuration or request values, including the
        one-time CSRF/confirmation values handled by the migration endpoint.
        Keep the diagnostic to the exception class and its terminal traceback
        location; Python tracebacks do not include local values at that point.
        """
        traceback = error.__traceback__
        while traceback is not None and traceback.tb_next is not None:
            traceback = traceback.tb_next
        if traceback is None:
            location = "unknown"
        else:
            code = traceback.tb_frame.f_code
            location = "{}:{}:{}".format(
                _safe_diagnostic_field(Path(code.co_filename).name, maximum_length=96),
                traceback.tb_lineno,
                _safe_diagnostic_field(code.co_name, maximum_length=96),
            )
        logging.getLogger("SeedSync.MigrationWeb").error(
            "Migration apply failed type=%s location=%s",
            _safe_diagnostic_field(type(error).__name__, maximum_length=96), location,
        )

    def _index(self) -> bottle.HTTPResponse:
        return self._serve_file("index.html")

    def _migration_fallback(self, spa_path: str) -> bottle.HTTPResponse:
        del spa_path
        return self._index()

    def _static(self, file_path: str) -> bottle.HTTPResponse:
        if file_path.startswith("server/"):
            bottle.abort(404)
        suffix = Path(file_path).suffix.lower()
        if suffix not in self._STATIC_SUFFIXES:
            bottle.abort(404)
        if "/" in file_path and file_path not in self._MIGRATION_ASSETS:
            bottle.abort(404)
        return self._serve_file(file_path)

    def _serve_file(self, file_path: str) -> bottle.HTTPResponse:
        candidate = (self._html_root / file_path).resolve()
        try:
            candidate.relative_to(self._html_root)
        except ValueError:
            bottle.abort(404)
        if not candidate.is_file():
            bottle.abort(404)
        return self._apply_security_headers(static_file(candidate.name, root=str(candidate.parent)))


class MigrationWebRuntime:
    """Blocking migration-only server that does not require normal Context or Config."""

    def __init__(
        self,
        *,
        bind_host: str,
        port: int,
        html_path: str,
        coordinator: MigrationCoordinator,
        allowed_origins: tuple[str, ...] = (),
    ) -> None:
        self._restart_timer: threading.Timer | None = None
        self.app = MigrationWebApp(
            html_path, coordinator,
            on_continue=self._schedule_normal_startup,
            allowed_origins=allowed_origins,
        )
        self._bind_host = bind_host
        self._port = port
        self._server: MyWSGIRefServer | None = None
        self._logger = logging.getLogger("SeedSync.MigrationWeb")

    def run(self) -> None:
        server = MyWSGIRefServer(self._logger, host=self._bind_host, port=self._port)
        self._server = server
        try:
            bottle.run(app=self.app, server=server, debug=False, quiet=True)
        finally:
            self._server = None

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()

    def _schedule_normal_startup(self) -> None:
        if self._restart_timer is not None:
            return
        # Allow the protected continue response to leave this WSGI server,
        # then return control to seedsync.py's existing reconstruction loop.
        timer = threading.Timer(0.15, self.stop)
        timer.daemon = True
        self._restart_timer = timer
        timer.start()
