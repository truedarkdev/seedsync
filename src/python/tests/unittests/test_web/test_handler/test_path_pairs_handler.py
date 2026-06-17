import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common import PathPair, PathPairConflictError
from web.handler.path_pairs import PathPairsHandler


class TestPathPairsHandlerCreateUpdate(unittest.TestCase):
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
        self.assertIn("activation failed", body["error"])
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
        self.assertIn("activation failed", body["error"])
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
