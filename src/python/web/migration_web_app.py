"""Read-only web runtime used while a selected configuration migration blocks startup."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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


def _safe_display_text(value: object, *, maximum_length: int) -> str:
    """Keep coordinator-owned display text bounded and free of control characters."""
    if not isinstance(value, str):
        return ""
    printable = "".join(character for character in value if character.isprintable())
    return printable[:maximum_length]


def migration_status_payload(decision: MigrationDecision) -> dict[str, Any]:
    """Serialize the deliberately non-mutating migration capability contract."""
    error: dict[str, str] | None = None
    if decision.state == MigrationState.FAILED:
        error = {
            "code": "migration_preflight_failed",
            "message": "SeedSync could not complete the migration readiness check.",
        }

    return {
        "schema_version": 1,
        "mode": "migration_required",
        "state": decision.state.value,
        "migration_id": _safe_display_text(decision.migration_id, maximum_length=160) or None,
        "source_schema": _safe_display_text(decision.source_schema, maximum_length=80) or None,
        "target_schema": _safe_display_text(decision.target_schema, maximum_length=80) or None,
        "features": [dict(feature) for feature in RELEASE_TOUR_FEATURES],
        "error": error,
        "retryable": bool(decision.retryable),
        "capabilities": {
            "apply": False,
            "retry": False,
            "restore": False,
        },
        "backup": {
            "required": True,
            "complete_restore_ready": False,
            "status": "not_ready",
        },
        "blocker": "complete_backup_restore_not_ready",
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

    def __init__(self, html_path: str, coordinator: MigrationCoordinator) -> None:
        super().__init__()
        self._html_root = Path(html_path).resolve()
        self._coordinator = coordinator

        self.hook("before_request")(self._deny_unregistered_server_routes)
        self.hook("after_request")(self._apply_security_headers)
        self.get("/")(self._redirect_to_migration)
        self.get("/server/migration/v1/status")(self._status)
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
            bottle.request.path != "/server/migration/v1/status"
            or bottle.request.method not in {"GET", "HEAD"}
        ):
            bottle.abort(404)

    @staticmethod
    def _redirect_to_migration() -> bottle.HTTPResponse:
        return bottle.redirect("/migration")

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
        payload = migration_status_payload(decision)
        return self._apply_security_headers(bottle.HTTPResponse(
            body=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            status=200,
            content_type="application/json; charset=utf-8",
        ))

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
    ) -> None:
        self.app = MigrationWebApp(html_path, coordinator)
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
