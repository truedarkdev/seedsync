# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
from unittest.mock import patch

from tests.integration.test_web.test_web_app import BaseTestWebApp


LEGACY_TEST_API_TOKEN = "legacy-test-token"


class TestPathPairsHandler(BaseTestWebApp):
    def test_get_all_initially_empty(self):
        response = self.test_app.get("/server/path-pairs")

        self.assertEqual(200, response.status_int)
        self.assertEqual({"success": True, "data": []}, json.loads(response.text))

    def test_legacy_token_cannot_read_path_pairs_list(self):
        legacy_client = type(self.test_app)(self.web_app, extra_environ={
            "HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)
        })

        response = legacy_client.get("/server/path-pairs", expect_errors=True)

        self.assertEqual(401, response.status_int)
        self.assertIn("Invalid API token", response.text)

    def test_create_and_get_one(self):
        response = self.test_app.post_json("/server/path-pairs", {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/local/movies",
            "enabled": True,
            "auto_queue": False
        })

        self.assertEqual(200, response.status_int)
        created = json.loads(response.text)["data"]
        self.assertEqual("Movies", created["name"])
        self.assertEqual("/remote/movies", created["remote_path"])
        self.assertEqual("/local/movies", created["local_path"])
        self.assertEqual(False, created["auto_queue"])

        fetched = self.test_app.get("/server/path-pairs/{}".format(created["id"]))
        self.assertEqual(200, fetched.status_int)
        self.assertEqual(created, json.loads(fetched.text)["data"])

    def test_legacy_token_cannot_read_single_path_pair(self):
        created = json.loads(self.test_app.post_json("/server/path-pairs", {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/local/movies",
            "enabled": True,
            "auto_queue": False
        }).text)["data"]
        legacy_client = type(self.test_app)(self.web_app, extra_environ={
            "HTTP_AUTHORIZATION": "Bearer {}".format(LEGACY_TEST_API_TOKEN)
        })

        response = legacy_client.get("/server/path-pairs/{}".format(created["id"]), expect_errors=True)

        self.assertEqual(401, response.status_int)
        self.assertIn("Invalid API token", response.text)

    def test_create_requires_remote_and_local_paths(self):
        response = self.test_app.post_json(
            "/server/path-pairs",
            {"name": "Invalid"},
            expect_errors=True
        )

        self.assertEqual(400, response.status_int)
        self.assertEqual(
            "remote_path and local_path are required",
            json.loads(response.text)["error"]
        )

    def test_create_rejects_wrong_field_types(self):
        response = self.test_app.post_json(
            "/server/path-pairs",
            {"name": "Invalid", "remote_path": 123, "local_path": []},
            expect_errors=True
        )

        self.assertEqual(400, response.status_int)
        self.assertEqual(
            "Path pair 'Invalid': remote_path must be a string",
            json.loads(response.text)["error"]
        )

    def test_create_rejects_invalid_json(self):
        response = self.test_app.post(
            "/server/path-pairs",
            "{bad json",
            content_type="application/json",
            expect_errors=True
        )

        self.assertEqual(400, response.status_int)
        self.assertIn("Invalid JSON:", json.loads(response.text)["error"])

    def test_update_existing_pair(self):
        created = json.loads(self.test_app.post_json("/server/path-pairs", {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/local/movies"
        }).text)["data"]
        self.controller.refresh_path_pairs.reset_mock()

        response = self.test_app.put_json("/server/path-pairs/{}".format(created["id"]), {
            "name": "Films",
            "enabled": False
        })

        self.assertEqual(200, response.status_int)
        updated = json.loads(response.text)["data"]
        self.assertEqual("Films", updated["name"])
        self.assertEqual(False, updated["enabled"])
        self.assertEqual("/remote/movies", updated["remote_path"])
        self.assertEqual("/local/movies", updated["local_path"])
        self.controller.refresh_path_pairs.assert_called_once_with(wait=True)

    def test_update_non_runtime_field_does_not_refresh_controller(self):
        created = json.loads(self.test_app.post_json("/server/path-pairs", {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/local/movies",
            "enabled": False,
            "auto_queue": True
        }).text)["data"]
        self.controller.refresh_path_pairs.reset_mock()

        response = self.test_app.put_json("/server/path-pairs/{}".format(created["id"]), {
            "auto_queue": False
        })

        self.assertEqual(200, response.status_int)
        self.controller.refresh_path_pairs.assert_not_called()

    def test_create_returns_validation_warnings(self):
        with patch("common.path_pair.is_running_in_docker", return_value=True):
            response = self.test_app.post_json("/server/path-pairs", {
                "name": "Movies",
                "remote_path": "/remote/movies",
                "local_path": "/media/movies"
            })

        self.assertEqual(200, response.status_int)
        payload = json.loads(response.text)
        self.assertEqual(1, len(payload["warnings"]))
        self.assertIn("/media/movies", payload["warnings"][0])

    def test_delete_existing_pair(self):
        created = json.loads(self.test_app.post_json("/server/path-pairs", {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/local/movies"
        }).text)["data"]

        response = self.test_app.delete("/server/path-pairs/{}".format(created["id"]))

        self.assertEqual(200, response.status_int)
        self.assertEqual(created["id"], json.loads(response.text)["data"]["deleted"])
        self.assertEqual([], self.context.path_pair_manager.get_all_pairs())

    def test_reorder_pairs(self):
        first = json.loads(self.test_app.post_json("/server/path-pairs", {
            "name": "Movies",
            "remote_path": "/remote/movies",
            "local_path": "/local/movies"
        }).text)["data"]
        second = json.loads(self.test_app.post_json("/server/path-pairs", {
            "name": "TV",
            "remote_path": "/remote/tv",
            "local_path": "/local/tv"
        }).text)["data"]

        response = self.test_app.post_json("/server/path-pairs/reorder", {
            "order": [second["id"], first["id"]]
        })

        self.assertEqual(200, response.status_int)
        data = json.loads(response.text)["data"]
        self.assertEqual([second["id"], first["id"]], [pair["id"] for pair in data])

    def test_get_missing_pair_returns_404(self):
        response = self.test_app.get("/server/path-pairs/missing", expect_errors=True)

        self.assertEqual(404, response.status_int)
        self.assertEqual(False, json.loads(response.text)["success"])
