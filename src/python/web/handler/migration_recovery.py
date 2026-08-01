"""Authenticated browser recovery for one completed configuration migration."""

from __future__ import annotations

import json
from typing import Callable, TypeGuard

import bottle
from bottle import HTTPResponse

from common import overrides
from migration import MigrationCoordinator
from ..web_app import IHandler, WebApp


def _is_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


class MigrationRecoveryHandler(IHandler):
    """Expose only receipt-bound recovery status and an explicit restore request."""

    _STATUS_PATH = "/server/admin/migration-recovery/v1/status"
    _RESTORE_PATH = "/server/admin/migration-recovery/v1/restore"

    def __init__(
        self, coordinator: MigrationCoordinator, request_restart: Callable[[], None],
    ) -> None:
        self._coordinator = coordinator
        self._request_restart = request_restart

    @staticmethod
    def _json(payload: object, status: int = 200) -> HTTPResponse:
        return HTTPResponse(
            body=json.dumps(payload, sort_keys=True, separators=(",", ":")), status=status,
            headers={"Content-Type": "application/json"},
        )

    @staticmethod
    def _request_json() -> dict[str, object]:
        try:
            raw = bottle.request.body.read().decode("utf-8")
            payload: object = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be a JSON object") from exc
        if not _is_object(payload):
            raise ValueError("Request body must be a JSON object")
        return payload

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_handler(self._STATUS_PATH, self._status, required_scope="admin")
        web_app.add_post_handler(self._RESTORE_PATH, self._restore, required_scope="admin")

    def _status(self) -> HTTPResponse:
        eligibility = self._coordinator.recovery_eligibility()
        # Receipt hashes bind the server-side intent only and are deliberately
        # not part of the browser contract.
        return self._json({
            key: value for key, value in eligibility.items()
            if key not in {"receipt_sha256", "backup_manifest_sha256"}
        })

    def _restore(self) -> HTTPResponse:
        try:
            payload = self._request_json()
            if set(payload) != {"confirmation", "other_instances_stopped"}:
                raise ValueError("Recovery request contains unknown fields")
            confirmation = payload.get("confirmation")
            other_instances_stopped = payload.get("other_instances_stopped")
            if not isinstance(confirmation, str) or type(other_instances_stopped) is not bool:
                raise ValueError("Recovery confirmation is invalid")
            self._coordinator.request_recovery_restore(
                confirmation=confirmation,
                other_instances_stopped=other_instances_stopped,
            )
            self._request_restart()
            return self._json({
                "accepted": True,
                "message": "SeedSync is stopping normal services and restoring the verified migration backup.",
            }, status=202)
        except (OSError, ValueError) as exc:
            return self._json({"error": str(exc)}, status=409)
