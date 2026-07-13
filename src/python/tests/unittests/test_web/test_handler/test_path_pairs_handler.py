import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common import PathPair, PathPairConflictError
from web.handler.path_pairs import PathPairsHandler


class TestPathPairsHandlerCreateUpdate(unittest.TestCase):
    REFRESH_FAILURE_MESSAGE = "Failed to apply path pair changes"

    def setUp(self):
        self.manager = MagicMock()
        self.controller = MagicMock()
        self.handler = PathPairsHandler(self.manager, controller=self.controller)

    def __mock_request(self, payload):
        return patch(
            "web.handler.path_pairs.bottle.request",
            SimpleNamespace(
                body=SimpleNamespace(
                    read=lambda: json.dumps(payload).encode("utf-8")
                )
            )
        )

    def __assert_bad_request(self, response, expected_error):
        self.assertEqual(400, response.status_code)
        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertEqual(expected_error, body["error"])

    def test_create_returns_error_response_when_runtime_refresh_fails(self):
        payload = {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/downloads/movies",
            "enabled": True,
            "auto_queue": True,
        }
        self.manager.add_pair.return_value = []
        self.controller.refresh_path_pairs.side_effect = RuntimeError("activation failed")

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_create()

        self.assertEqual(500, response.status_code)
        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertEqual(self.REFRESH_FAILURE_MESSAGE, body["error"])
        self.manager.add_pair.assert_called_once()
        self.controller.refresh_path_pairs.assert_called_once_with(wait=True)

    def test_create_returns_409_for_duplicate_name(self):
        payload = {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/downloads/movies",
            "enabled": True,
            "auto_queue": True,
        }
        self.manager.add_pair.side_effect = PathPairConflictError(
            "Path pair with name 'Movies' already exists"
        )

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_create()

        self.assertEqual(409, response.status_code)
        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertEqual("Path pair with name 'Movies' already exists", body["error"])
        self.controller.refresh_path_pairs.assert_not_called()

    def test_update_returns_error_response_when_runtime_refresh_fails(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        payload = {
            "name": "Movies",
            "remote_path": "/remote/new-movies",
            "local_path": "/downloads/new-movies",
            "enabled": True,
            "auto_queue": True,
        }
        self.manager.get_pair_by_id.return_value = existing
        self.manager.update_pair.return_value = []
        self.controller.refresh_path_pairs.side_effect = RuntimeError("activation failed")

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.assertEqual(500, response.status_code)
        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertEqual(self.REFRESH_FAILURE_MESSAGE, body["error"])
        self.manager.update_pair.assert_called_once()
        self.controller.refresh_path_pairs.assert_called_once_with(wait=True)

    def test_update_returns_409_for_duplicate_name(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        payload = {
            "name": "TV",
            "remote_path": "/remote/new-movies",
            "local_path": "/downloads/new-movies",
            "enabled": True,
            "auto_queue": True,
        }
        self.manager.get_pair_by_id.return_value = existing
        self.manager.update_pair.side_effect = PathPairConflictError(
            "Path pair with name 'TV' already exists"
        )

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.assertEqual(409, response.status_code)
        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertEqual("Path pair with name 'TV' already exists", body["error"])
        self.controller.refresh_path_pairs.assert_not_called()

    def test_create_rejects_non_object_json_body(self):
        with self.__mock_request([]):
            response = self.handler._PathPairsHandler__handle_create()

        self.__assert_bad_request(response, "Path pair request body must be a JSON object")
        self.manager.add_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_create_rejects_wrong_type_id_field(self):
        payload = {
            "id": 123,
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/downloads/movies",
        }

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_create()

        self.__assert_bad_request(response, "Path pair 'Movies': id must be a string")
        self.manager.add_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_create_rejects_wrong_type_remote_path_field(self):
        payload = {
            "name": "Movies",
            "remote_path": 123,
            "local_path": "/downloads/movies",
        }

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_create()

        self.__assert_bad_request(response, "Path pair 'Movies': remote_path must be a string")
        self.manager.add_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_create_rejects_wrong_type_name_field(self):
        payload = {
            "name": 123,
            "remote_path": "/remote/movies",
            "local_path": "/downloads/movies",
        }

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_create()

        self.__assert_bad_request(response, "Path pair '<unnamed>': name must be a string")
        self.manager.add_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_update_rejects_non_object_json_body(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        self.manager.get_pair_by_id.return_value = existing

        with self.__mock_request([]):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.__assert_bad_request(response, "Path pair request body must be a JSON object")
        self.manager.update_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_update_rejects_wrong_type_id_field(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        payload = {
            "id": 123,
            "enabled": False,
        }
        self.manager.get_pair_by_id.return_value = existing

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.__assert_bad_request(response, "Path pair 'Movies': id must be a string")
        self.manager.update_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_update_rejects_wrong_type_enabled_field(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        payload = {
            "enabled": "yes",
        }
        self.manager.get_pair_by_id.return_value = existing

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.__assert_bad_request(response, "Path pair 'Movies': enabled must be a boolean")
        self.manager.update_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_update_rejects_wrong_type_local_path_field(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        payload = {
            "local_path": [],
        }
        self.manager.get_pair_by_id.return_value = existing

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.__assert_bad_request(response, "Path pair 'Movies': local_path must be a string")
        self.manager.update_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_update_rejects_wrong_type_auto_queue_field(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        payload = {
            "auto_queue": "yes",
        }
        self.manager.get_pair_by_id.return_value = existing

        with self.__mock_request(payload):
            response = self.handler._PathPairsHandler__handle_update("movies")

        self.__assert_bad_request(response, "Path pair 'Movies': auto_queue must be a boolean")
        self.manager.update_pair.assert_not_called()
        self.controller.refresh_path_pairs.assert_not_called()

    def test_reorder_rejects_non_string_id_without_mutating_order(self):
        with self.__mock_request({"order": ["movies", 7]}):
            response = self.handler._PathPairsHandler__handle_reorder()

        self.__assert_bad_request(response, "order field must be a list of path pair IDs")
        self.manager.reorder_pairs.assert_not_called()

    def test_delete_refresh_failure_returns_generic_message(self):
        existing = PathPair(
            id="movies",
            name="Movies",
            remote_path="/remote/movies",
            local_path="/downloads/movies",
            enabled=True,
            auto_queue=True,
        )
        self.manager.get_pair_by_id.return_value = existing
        self.controller.refresh_path_pairs.side_effect = RuntimeError("activation failed")

        response = self.handler._PathPairsHandler__handle_delete("movies")

        self.assertEqual(500, response.status_code)
        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertEqual(self.REFRESH_FAILURE_MESSAGE, body["error"])
        self.manager.remove_pair.assert_called_once_with("movies")
        self.controller.refresh_path_pairs.assert_called_once_with(wait=True)
