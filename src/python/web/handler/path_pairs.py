# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
from dataclasses import asdict

import bottle
from bottle import HTTPResponse

from common import PathPairManager, PathPair, PathPairError, PersistError, overrides
from ..web_app import IHandler, WebApp


class PathPairsHandler(IHandler):
    def __init__(self, path_pair_manager: PathPairManager, controller=None):
        self.__path_pair_manager = path_pair_manager
        self.__controller = controller

    @staticmethod
    def __json_response(payload, status: int = 200):
        return HTTPResponse(
            body=json.dumps(payload),
            status=status,
            headers={"Content-Type": "application/json"}
        )

    @staticmethod
    def __load_request_json():
        return json.loads(bottle.request.body.read().decode("utf-8"))

    @overrides(IHandler)
    def add_routes(self, web_app: WebApp):
        web_app.add_handler(
            "/server/path-pairs",
            self.__handle_get_all,
            required_scope="read"
        )
        web_app.get(
            "/server/path-pairs/<pair_id>",
            required_scope="read"
        )(self.__handle_get_one)
        web_app.add_post_handler(
            "/server/path-pairs",
            self.__handle_create,
            required_scope="write"
        )
        web_app.add_put_handler(
            "/server/path-pairs/<pair_id>",
            self.__handle_update,
            required_scope="write"
        )
        web_app.add_delete_handler(
            "/server/path-pairs/<pair_id>",
            self.__handle_delete,
            required_scope="write"
        )
        web_app.add_post_handler(
            "/server/path-pairs/reorder",
            self.__handle_reorder,
            required_scope="write"
        )

    def __handle_get_all(self):
        pairs = [asdict(pair) for pair in self.__path_pair_manager.get_all_pairs()]
        return self.__json_response({"success": True, "data": pairs})

    def __handle_get_one(self, pair_id: str):
        pair = self.__path_pair_manager.get_pair_by_id(pair_id)
        if pair is None:
            return self.__json_response(
                {"success": False, "error": "Path pair with id '{}' not found".format(pair_id)},
                status=404
            )
        return self.__json_response({"success": True, "data": asdict(pair)})

    def __handle_create(self):
        try:
            data = self.__load_request_json()
            if "remote_path" not in data or "local_path" not in data:
                return self.__json_response(
                    {"success": False, "error": "remote_path and local_path are required"},
                    status=400
                )

            pair = PathPair(
                name=data.get("name", ""),
                remote_path=data["remote_path"],
                local_path=data["local_path"],
                enabled=data.get("enabled", True),
                auto_queue=data.get("auto_queue", True)
            )
            warnings = self.__path_pair_manager.add_pair(pair)
            if self.__controller is not None and pair.enabled:
                try:
                    self.__controller.refresh_path_pairs(wait=True)
                except Exception as exc:
                    return self.__json_response(
                        {"success": False, "error": "Failed to apply path pair changes: {}".format(exc)},
                        status=500
                    )
            return self.__json_response({"success": True, "data": asdict(pair), "warnings": warnings})
        except ValueError as exc:
            return self.__json_response({"success": False, "error": "Invalid JSON: {}".format(exc)}, status=400)
        except PathPairError as exc:
            return self.__json_response({"success": False, "error": str(exc)}, status=400)
        except PersistError as exc:
            return self.__json_response({"success": False, "error": "Failed to save: {}".format(exc)}, status=500)

    def __handle_update(self, pair_id: str):
        try:
            existing = self.__path_pair_manager.get_pair_by_id(pair_id)
            if existing is None:
                return self.__json_response(
                    {"success": False, "error": "Path pair with id '{}' not found".format(pair_id)},
                    status=404
                )

            data = self.__load_request_json()
            pair = PathPair(
                id=pair_id,
                name=data.get("name", existing.name),
                remote_path=data.get("remote_path", existing.remote_path),
                local_path=data.get("local_path", existing.local_path),
                enabled=data.get("enabled", existing.enabled),
                auto_queue=data.get("auto_queue", existing.auto_queue)
            )
            warnings = self.__path_pair_manager.update_pair(pair)
            if self.__controller is not None and self.__update_affects_runtime(existing, pair):
                try:
                    self.__controller.refresh_path_pairs(wait=True)
                except Exception as exc:
                    return self.__json_response(
                        {"success": False, "error": "Failed to apply path pair changes: {}".format(exc)},
                        status=500
                    )
            return self.__json_response({"success": True, "data": asdict(pair), "warnings": warnings})
        except ValueError as exc:
            return self.__json_response({"success": False, "error": "Invalid JSON: {}".format(exc)}, status=400)
        except PathPairError as exc:
            return self.__json_response({"success": False, "error": str(exc)}, status=400)
        except PersistError as exc:
            return self.__json_response({"success": False, "error": "Failed to save: {}".format(exc)}, status=500)

    def __handle_delete(self, pair_id: str):
        try:
            existing = self.__path_pair_manager.get_pair_by_id(pair_id)
            self.__path_pair_manager.remove_pair(pair_id)
            if self.__controller is not None and existing is not None and existing.enabled:
                try:
                    self.__controller.refresh_path_pairs(wait=True)
                except Exception as exc:
                    return self.__json_response(
                        {"success": False, "error": "Failed to apply path pair changes: {}".format(exc)},
                        status=500
                    )
            return self.__json_response({"success": True, "data": {"deleted": pair_id}})
        except PathPairError as exc:
            return self.__json_response({"success": False, "error": str(exc)}, status=404)
        except PersistError as exc:
            return self.__json_response({"success": False, "error": "Failed to save: {}".format(exc)}, status=500)

    def __handle_reorder(self):
        try:
            data = self.__load_request_json()
            order = data.get("order")
            if not isinstance(order, list):
                return self.__json_response(
                    {"success": False, "error": "order field must be a list of path pair IDs"},
                    status=400
                )

            self.__path_pair_manager.reorder_pairs(order)
            pairs = [asdict(pair) for pair in self.__path_pair_manager.get_all_pairs()]
            return self.__json_response({"success": True, "data": pairs})
        except ValueError as exc:
            return self.__json_response({"success": False, "error": "Invalid JSON: {}".format(exc)}, status=400)
        except PathPairError as exc:
            return self.__json_response({"success": False, "error": str(exc)}, status=400)
        except PersistError as exc:
            return self.__json_response({"success": False, "error": "Failed to save: {}".format(exc)}, status=500)

    @staticmethod
    def __update_affects_runtime(existing: PathPair, updated: PathPair) -> bool:
        if existing.enabled != updated.enabled:
            return True
        if not existing.enabled and not updated.enabled:
            return False
        return any((
            existing.name != updated.name,
            existing.remote_path != updated.remote_path,
            existing.local_path != updated.local_path,
        ))
