# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
from typing import TypeGuard

import bottle
from bottle import HTTPResponse

from common import Config, overrides
from ..auth_store import ApiKeyStore
from ..web_app import IHandler, WebApp


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_string_list(value: object) -> TypeGuard[list[str]]:
    return _is_object_list(value) and all(isinstance(item, str) for item in value)


class AdminHandler(IHandler):
    _UI_SESSION_COOKIE_NAME = "seedsync_ui_session"
    _BOOTSTRAP_EXCHANGE_COOKIE_NAME = "seedsync_bootstrap_exchange"

    def __init__(self, config: Config, auth_store: ApiKeyStore) -> None:
        self.__config = config
        self.__auth_store = auth_store

    @staticmethod
    def __secure_browser_cookie() -> bool:
        forwarded_proto = bottle.request.headers.get("X-Forwarded-Proto", "").strip().lower()
        if forwarded_proto in {"http", "https"}:
            return forwarded_proto == "https"
        return bottle.request.urlparts.scheme.lower() == "https"

    @staticmethod
    def __json_response(payload: object, status: int = 200) -> HTTPResponse:
        return HTTPResponse(
            body=json.dumps(payload),
            status=status,
            headers={"Content-Type": "application/json"}
        )

    @staticmethod
    def __load_request_json() -> dict[str, object]:
        raw_body = bottle.request.body.read().decode("utf-8")
        if not raw_body.strip():
            return {}
        value: object = json.loads(raw_body)
        if not _is_object_dict(value):
            raise ValueError("Request body must be a JSON object")
        return value

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp) -> None:
        web_app.add_post_handler(
            "/server/admin/bootstrap/v1/exchange",
            self.__handle_exchange_bootstrap_proof,
            required_scope="read",
            allow_bootstrap_proof_exchange=True
        )
        web_app.add_post_handler(
            "/server/admin/bootstrap/v1/first-api-key",
            self.__handle_bootstrap_first_api_key,
            required_scope="admin",
            allow_first_admin_bootstrap=True
        )
        web_app.add_post_handler(
            "/server/browser/v1/remember",
            self.__handle_remember_browser_session,
            required_scope="read",
            allow_browser_api_key_entry=True
        )

        web_app.add_handler("/server/admin/api-keys/v1", self.__handle_list_api_keys, required_scope="admin")
        web_app.add_post_handler("/server/admin/api-keys/v1", self.__handle_create_api_key, required_scope="admin")
        web_app.add_put_handler("/server/admin/api-keys/v1/<key_id>", self.__handle_update_api_key, required_scope="admin")
        web_app.add_delete_handler("/server/admin/api-keys/v1/<key_id>", self.__handle_delete_api_key, required_scope="admin")
        web_app.add_post_handler(
            "/server/admin/api-keys/v1/<key_id>/revoke",
            self.__handle_revoke_api_key,
            required_scope="admin"
        )
        web_app.add_post_handler(
            "/server/admin/api-keys/v1/<key_id>/rotate",
            self.__handle_rotate_api_key,
            required_scope="admin"
        )

    def __handle_exchange_bootstrap_proof(self) -> HTTPResponse:
        if self.__auth_store.active_admin_key_count > 0:
            return self.__json_response({"error": "Bootstrap proof exchange is only available before the first admin API key exists"}, status=409)

        try:
            data = self.__load_request_json()
            proof = data.get("proof", "")
            if not isinstance(proof, str) or not proof.strip():
                return self.__json_response({"error": "Bootstrap proof is required"}, status=400)

            proof = proof.strip()
            exchange_secret = bottle.request.get_cookie(self._BOOTSTRAP_EXCHANGE_COOKIE_NAME)
            peek_exchange = getattr(self.__auth_store, "peek_bootstrap_exchange", None)
            consume_exchange = getattr(self.__auth_store, "consume_bootstrap_exchange", None)
            if peek_exchange is None or consume_exchange is None or not peek_exchange(exchange_secret):
                return self.__json_response({"error": "Bootstrap exchange grant is invalid or has expired"}, status=401)

            if not self.__auth_store.peek_bootstrap_proof(proof):
                return self.__json_response({"error": "Bootstrap proof is invalid or has already been used"}, status=401)

            if not consume_exchange(exchange_secret):
                return self.__json_response({"error": "Bootstrap exchange grant is invalid or has expired"}, status=401)
            if not self.__auth_store.consume_bootstrap_proof(proof):
                return self.__json_response({"error": "Bootstrap proof is invalid or has already been used"}, status=401)

            ui_session = self.__auth_store.create_ui_session(["bootstrap"], bootstrap=True)
            response = self.__json_response({
                "expires_at": ui_session.expires_at,
            })
            response.delete_cookie(self._BOOTSTRAP_EXCHANGE_COOKIE_NAME, path="/")
            response.set_cookie(
                self._UI_SESSION_COOKIE_NAME,
                ui_session.secret,
                path="/",
                httponly=True,
                samesite="strict",
                secure=self.__secure_browser_cookie(),
                max_age=ui_session.cookie_max_age_seconds(),
            )
            return response
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_bootstrap_first_api_key(self) -> HTTPResponse:
        completed_migration_transaction_started = False
        try:
            handover_version = self.__auth_store.effective_browser_handover_version(self.__config)
            # Complete-migration marker prerequisites must fail before key,
            # session, or history persistence can begin.
            self.__auth_store.validate_completed_migration_claim_transition(handover_version)
            completed_migration_transaction_started = self.__auth_store.begin_completed_migration_claim_transaction()
            data = self.__load_request_json()
            name = data.get("name", "bootstrap-admin")
            if not isinstance(name, str):
                raise ValueError("API key name must be a string")
            result = self.__auth_store.create_initial_admin_api_key_if_available(
                browser_handover_version=handover_version,
                name=name,
            )
            if result is None:
                if completed_migration_transaction_started:
                    self.__auth_store.abort_completed_migration_claim_transaction()
                return self.__json_response({
                    "error": "First-admin browser bootstrap is only available when the initial handover window is open"
                }, status=409)

            ui_session = self.__auth_store.create_remembered_browser_session_for_api_key(result["record"].id)
            self.__auth_store.complete_completed_migration_claim_transition(
                result["record"].id, handover_version,
            )
            if completed_migration_transaction_started:
                self.__auth_store.finish_completed_migration_claim_transaction()
                completed_migration_transaction_started = False
                self.__auth_store.finalize_browser_handover_claim(handover_version)
            response = self.__json_response({
                "key": result["record"].to_public_dict(),
                "secret": result["secret"],
                "browser_handover": self.__auth_store.get_browser_handover_state(self.__config),
            }, status=201)
            response.set_cookie(
                self._UI_SESSION_COOKIE_NAME,
                ui_session.secret,
                path="/",
                httponly=True,
                samesite="strict",
                secure=self.__secure_browser_cookie(),
                max_age=ui_session.cookie_max_age_seconds(),
            )
            return response
        except (OSError, TypeError, ValueError) as exc:
            if completed_migration_transaction_started:
                try:
                    self.__auth_store.abort_completed_migration_claim_transaction()
                except (OSError, ValueError):
                    return self.__json_response({
                        "error": "Completed migration first claim could not be rolled back safely"
                    }, status=500)
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_remember_browser_session(self) -> HTTPResponse:
        try:
            data = self.__load_request_json()
            api_key_secret = data.get("secret", "")
            if not isinstance(api_key_secret, str) or not api_key_secret.strip():
                return self.__json_response({"error": "API key secret is required"}, status=400)

            auth_record = self.__auth_store.find_api_key_by_secret(api_key_secret.strip())
            if auth_record is None:
                return self.__json_response({"error": "API key secret is invalid or has been revoked"}, status=401)
            if getattr(auth_record, "revoked_at", None) is not None:
                return self.__json_response({"error": "API key has been revoked"}, status=403)

            ui_session = self.__auth_store.create_remembered_browser_session_for_api_key(auth_record.id)
            response = self.__json_response({
                "key": auth_record.to_public_dict(),
                "browser_handover": self.__auth_store.get_browser_handover_state(self.__config),
                "remembered": True,
                "expires_at": ui_session.expires_at or None,
                "cookie_max_age_seconds": ui_session.cookie_max_age_seconds(),
            }, status=201)
            response.set_cookie(
                self._UI_SESSION_COOKIE_NAME,
                ui_session.secret,
                path="/",
                httponly=True,
                samesite="strict",
                secure=self.__secure_browser_cookie(),
                max_age=ui_session.cookie_max_age_seconds(),
            )
            return response
        except (TypeError, ValueError, KeyError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_list_api_keys(self) -> HTTPResponse:
        include_revoked = AdminHandler.__query_flag("include_revoked")
        return self.__json_response({"keys": self.__auth_store.list_api_keys(include_revoked=include_revoked)})

    def __handle_create_api_key(self) -> HTTPResponse:
        try:
            data = self.__load_request_json()
            name = data.get("name", "")
            scopes = data.get("scopes", [])
            if not isinstance(name, str):
                raise ValueError("API key name must be a string")
            if not _is_string_list(scopes):
                raise ValueError("API key scopes must be a list of strings")
            result = self.__auth_store.create_api_key(
                name=name,
                scopes=scopes,
            )
            payload = {
                "key": result["record"].to_public_dict(),
                "secret": result["secret"],
            }
            return self.__json_response(payload, status=201)
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_update_api_key(self, key_id: str) -> HTTPResponse:
        try:
            data = self.__load_request_json()
            scopes = data.get("scopes")
            if scopes is not None and not _is_string_list(scopes):
                raise ValueError("API key scopes must be a list of strings")
            record = self.__auth_store.update_api_key(
                key_id=key_id,
                name=data.get("name"),
                scopes=scopes,
            )
            return self.__json_response({"key": record.to_public_dict()})
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_delete_api_key(self, key_id: str) -> HTTPResponse:
        try:
            self.__auth_store.delete_api_key(key_id)
            return HTTPResponse(status=204)
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_revoke_api_key(self, key_id: str) -> HTTPResponse:
        try:
            record = self.__auth_store.revoke_api_key(key_id)
            return self.__json_response({"key": record.to_public_dict()})
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_rotate_api_key(self, key_id: str) -> HTTPResponse:
        try:
            result = self.__auth_store.rotate_api_key(key_id)
            return self.__json_response({
                "key": result["record"].to_public_dict(),
                "secret": result["secret"],
            })
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    @staticmethod
    def __query_flag(name: str) -> bool:
        value = bottle.request.query.get(name)
        if value is None:
            return False
        return value.strip().lower() in {"1", "true", "yes", "on"}
