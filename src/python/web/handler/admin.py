# Copyright 2026, SeedSync Contributors, All rights reserved.

import json

import bottle
from bottle import HTTPResponse

from common import Config, overrides
from ..auth_store import ApiKeyStore
from ..web_app import IHandler, WebApp


class AdminHandler(IHandler):
    def __init__(self, config: Config, auth_store: ApiKeyStore):
        self.__config = config
        self.__auth_store = auth_store

    @staticmethod
    def __json_response(payload, status: int = 200):
        return HTTPResponse(
            body=json.dumps(payload),
            status=status,
            headers={"Content-Type": "application/json"}
        )

    @staticmethod
    def __load_request_json():
        raw_body = bottle.request.body.read().decode("utf-8")
        if not raw_body.strip():
            return {}
        return json.loads(raw_body)

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
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
        web_app.add_handler(
            "/server/admin/migration/v1",
            self.__handle_get_migration_state,
            required_scope="admin",
            allow_first_admin_bootstrap=True
        )
        web_app.add_post_handler(
            "/server/admin/migration/v1/legacy-api-token/disable",
            self.__handle_disable_legacy_token,
            required_scope="admin"
        )
        web_app.add_post_handler(
            "/server/admin/migration/v1/legacy-api-token/clear",
            self.__handle_clear_legacy_token,
            required_scope="admin"
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

    def __handle_get_migration_state(self):
        return self.__json_response(self.__auth_store.get_migration_state(self.__config))

    def __handle_exchange_bootstrap_proof(self):
        if self.__auth_store.active_admin_key_count > 0:
            return self.__json_response({"error": "Bootstrap proof exchange is only available before the first admin API key exists"}, status=409)

        try:
            data = self.__load_request_json()
            proof = data.get("proof", "")
            if not isinstance(proof, str) or not proof.strip():
                return self.__json_response({"error": "Bootstrap proof is required"}, status=400)

            proof = proof.strip()
            exchange_secret = bottle.request.get_cookie(WebApp._BOOTSTRAP_EXCHANGE_COOKIE_NAME)
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
            response.delete_cookie(WebApp._BOOTSTRAP_EXCHANGE_COOKIE_NAME, path="/")
            response.set_cookie(
                WebApp._UI_SESSION_COOKIE_NAME,
                ui_session.secret,
                path="/",
                httponly=True,
                samesite="strict",
            )
            return response
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_bootstrap_first_api_key(self):
        try:
            handover_version = self.__browser_handover_version()
            data = self.__load_request_json()
            result = self.__auth_store.create_initial_admin_api_key_if_available(
                browser_handover_version=handover_version,
                name=data.get("name", "bootstrap-admin"),
            )
            if result is None:
                return self.__json_response({
                    "error": "First-admin browser bootstrap is only available when the initial handover window is open"
                }, status=409)

            ui_session = self.__auth_store.create_browser_session_for_api_key(result["record"].id)
            response = self.__json_response({
                "key": result["record"].to_public_dict(),
                "secret": result["secret"],
                "browser_handover": self.__auth_store.get_browser_handover_state(self.__config),
            }, status=201)
            response.set_cookie(
                WebApp._UI_SESSION_COOKIE_NAME,
                ui_session.secret,
                path="/",
                httponly=True,
                samesite="strict",
            )
            return response
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_remember_browser_session(self):
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

            ui_session = self.__auth_store.create_browser_session_for_api_key(auth_record.id)
            response = self.__json_response({
                "key": auth_record.to_public_dict(),
                "browser_handover": self.__auth_store.get_browser_handover_state(self.__config),
                "expires_at": ui_session.expires_at,
            }, status=201)
            response.set_cookie(
                WebApp._UI_SESSION_COOKIE_NAME,
                ui_session.secret,
                path="/",
                httponly=True,
                samesite="strict",
            )
            return response
        except (TypeError, ValueError, KeyError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_disable_legacy_token(self):
        self.__auth_store.set_legacy_api_token_compatibility_enabled(False)
        return self.__json_response(self.__auth_store.get_migration_state(self.__config))

    def __handle_clear_legacy_token(self):
        self.__config.general.api_token = ""
        self.__auth_store.set_legacy_api_token_compatibility_enabled(False)
        return self.__json_response(self.__auth_store.get_migration_state(self.__config))

    def __handle_list_api_keys(self):
        include_revoked = AdminHandler.__query_flag("include_revoked")
        return self.__json_response({"keys": self.__auth_store.list_api_keys(include_revoked=include_revoked)})

    def __handle_create_api_key(self):
        try:
            data = self.__load_request_json()
            result = self.__auth_store.create_api_key(
                name=data.get("name", ""),
                scopes=data.get("scopes", [])
            )
            payload = {
                "key": result["record"].to_public_dict(),
                "secret": result["secret"],
            }
            return self.__json_response(payload, status=201)
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_update_api_key(self, key_id: str):
        try:
            data = self.__load_request_json()
            record = self.__auth_store.update_api_key(
                key_id=key_id,
                name=data.get("name"),
                scopes=data.get("scopes")
            )
            return self.__json_response({"key": record.to_public_dict()})
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_delete_api_key(self, key_id: str):
        try:
            self.__auth_store.delete_api_key(key_id)
            return HTTPResponse(status=204)
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_revoke_api_key(self, key_id: str):
        try:
            record = self.__auth_store.revoke_api_key(key_id)
            return self.__json_response({"key": record.to_public_dict()})
        except KeyError as exc:
            return self.__json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return self.__json_response({"error": str(exc)}, status=400)

    def __handle_rotate_api_key(self, key_id: str):
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

    def __browser_handover_version(self) -> str:
        general_config = getattr(self.__config, "general", None)
        if general_config is None:
            return ""
        handover_version = getattr(general_config, "browser_handover_recovery_version", "")
        return handover_version if isinstance(handover_version, str) else ""

    @staticmethod
    def __query_flag(name: str) -> bool:
        value = bottle.request.query.get(name)
        if value is None:
            return False
        return value.strip().lower() in {"1", "true", "yes", "on"}
