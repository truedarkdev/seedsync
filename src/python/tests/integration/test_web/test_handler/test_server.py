# Copyright 2017, Inderpreet Singh, All rights reserved.

from tests.integration.test_web.test_web_app import BaseTestWebApp


class TestServerHandler(BaseTestWebApp):
    def test_restart(self):
        self.assertFalse(self.web_app_builder.server_handler.is_restart_requested())
        response = self.test_app.post("/server/command/restart")
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.web_app_builder.server_handler.is_restart_requested())
        response = self.test_app.post("/server/command/restart")
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.web_app_builder.server_handler.is_restart_requested())

    def test_restart_rejects_get(self):
        response = self.test_app.get("/server/command/restart", expect_errors=True)
        self.assertEqual(404, response.status_code)

        response = self.test_app.post("/server/command/restart")
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.web_app_builder.server_handler.is_restart_requested())
