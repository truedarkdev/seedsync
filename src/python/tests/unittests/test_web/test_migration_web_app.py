import json
import io
import logging
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from migration import MigrationCoordinator, MigrationDecision, MigrationFeature, MigrationState
from web.migration_web_app import MigrationWebApp, MigrationWebRuntime, migration_status_payload
from web.web_app_job import MyWSGIRefServer
from webtest import TestApp, TestRequest


class TestMigrationWebApp(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        html_root = Path(self.temp_dir.name)
        (html_root / "index.html").write_text("<html><app-root></app-root></html>", encoding="utf-8")
        (html_root / "main.js").write_text("console.log('migration');", encoding="utf-8")
        (html_root / "assets" / "migration").mkdir(parents=True)
        (html_root / "assets" / "migration" / "progress.png").write_bytes(b"synthetic")
        (html_root / "assets" / "private.png").write_bytes(b"private")
        self.decision = MigrationDecision(
            state=MigrationState.REQUIRED,
            migration_id="original-v0.8.6-to-current-v1",
            source_schema="original-v0.8.6",
            target_schema="current-v1",
            features=(MigrationFeature("Path pairs", "Preserve configured transfer roots."),),
        )
        self.coordinator = MagicMock()
        self.coordinator.status.return_value = self.decision
        self.coordinator.retained_backup_ready.return_value = False
        self.on_success = MagicMock()
        self.app = MigrationWebApp(
            str(html_root), self.coordinator, on_success=self.on_success,
        )
        self.client = TestApp(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_status_exposes_versioned_guarded_apply_contract(self) -> None:
        response = self.client.get("/server/migration/v1/status")

        self.assertEqual(200, response.status_int)
        self.assertEqual(migration_status_payload(
            self.decision, csrf_token=self.app._csrf_token,
        ), json.loads(response.text))
        self.assertEqual(
            {"apply": True, "retry": False, "restore": False},
            response.json["capabilities"],
        )
        self.assertEqual(
            {"required": True, "complete_restore_ready": False, "status": "created_before_apply"},
            response.json["backup"],
        )
        self.assertIsNone(response.json["blocker"])
        self.assertEqual(
            "MIGRATE original-v0.8.6-to-current-v1",
            response.json["action"]["confirmation"],
        )
        self.assertEqual(
            [
                "path-pairs",
                "accurate-progress",
                "secure-access",
                "notifications",
                "transfer-choices",
                "historical-logs",
            ],
            [feature["key"] for feature in response.json["features"]],
        )
        self.assertEqual("Sync more than one folder", response.json["features"][0]["title"])
        self.assertNotIn("Preserve configured transfer roots", response.text)
        self.assertEqual("no-store, max-age=0", response.headers["Cache-Control"])
        self.assertEqual("DENY", response.headers["X-Frame-Options"])

    def test_failed_status_does_not_expose_coordinator_error_details(self) -> None:
        self.coordinator.status.return_value = MigrationDecision(
            state=MigrationState.FAILED,
            migration_id=self.decision.migration_id,
            source_schema=self.decision.source_schema,
            error=r"failed while reading C:\Users\person\secret\settings.cfg",
            retryable=True,
        )

        response = self.client.get("/server/migration/v1/status")

        self.assertEqual(
            {
                "code": "migration_apply_failed",
                "message": "The migration attempt did not complete. The retained backup remains available.",
            },
            response.json["error"],
        )
        self.assertNotIn("C:\\Users", response.text)
        self.assertTrue(response.json["retryable"])
        self.assertTrue(response.json["capabilities"]["retry"])

    def test_capabilities_follow_exact_durable_state(self) -> None:
        for state in (MigrationState.RUNNING, MigrationState.COMPLETE):
            with self.subTest(state=state.value):
                self.coordinator.status.return_value = MigrationDecision(state=state, retryable=True)
                payload = self.client.get("/server/migration/v1/status").json
                self.assertEqual(state.value, payload["state"])
                self.assertEqual({"apply": False, "retry": False, "restore": False}, payload["capabilities"])

        self.coordinator.status.return_value = MigrationDecision(
            state=MigrationState.FAILED,
            migration_id=self.decision.migration_id,
            retryable=True,
        )
        payload = self.client.get("/server/migration/v1/status").json
        self.assertEqual({"apply": False, "retry": True, "restore": False}, payload["capabilities"])

    def test_status_failure_returns_generic_non_leaky_failed_contract(self) -> None:
        self.coordinator.status.side_effect = OSError(r"cannot open C:\private\settings.cfg")

        response = self.client.get("/server/migration/v1/status")

        self.assertEqual(200, response.status_int)
        self.assertEqual("failed", response.json["state"])
        self.assertEqual("migration_preflight_failed", response.json["error"]["code"])
        self.assertNotIn("C:\\private", response.text)

    def test_root_redirects_and_migration_route_serves_spa(self) -> None:
        redirect = self.client.get("/", status=302)
        self.assertTrue(redirect.headers["Location"].endswith("/migration"))
        migration_page = self.client.get("/migration")
        self.assertIn("<app-root>", migration_page.text)
        self.assertEqual("no-store, max-age=0", migration_page.headers["Cache-Control"])
        content_security_policy = migration_page.headers["Content-Security-Policy"]
        self.assertEqual(MigrationWebApp._CONTENT_SECURITY_POLICY, content_security_policy)
        self.assertIn("base-uri 'self'", content_security_policy)
        self.assertNotIn("base-uri 'none'", content_security_policy)
        self.assertIn("frame-ancestors 'none'", content_security_policy)
        self.assertIn("<app-root>", self.client.get("/migration/checkpoint").text)
        self.assertIn("migration", self.client.get("/main.js").text)
        self.assertEqual(200, self.client.get("/assets/migration/progress.png").status_int)
        self.assertEqual(404, self.client.get("/assets/private.png", expect_errors=True).status_int)

    def test_migration_entry_uses_index_from_configured_distribution_root(self) -> None:
        distribution_root = Path(self.temp_dir.name) / "app" / "html"
        distribution_root.mkdir(parents=True)
        (distribution_root / "index.html").write_text(
            "<html><app-root>built migration entry</app-root></html>",
            encoding="utf-8",
        )
        app = TestApp(MigrationWebApp(str(distribution_root), self.coordinator))

        response = app.get("/migration")

        self.assertEqual(200, response.status_int)
        self.assertIn("built migration entry", response.text)

    def test_normal_server_routes_and_non_migration_pages_are_absent(self) -> None:
        for path in (
            "/server/status",
            "/server/config",
            "/server/admin/bootstrap/v1/status",
            "/server/stream",
            "/settings",
            "/bootstrap",
        ):
            with self.subTest(path=path):
                self.assertEqual(404, self.client.get(path, expect_errors=True).status_int)

        self.assertEqual(404, self.client.get(
            "/server/migration/v1/apply", expect_errors=True,
        ).status_int)
        self.assertEqual(
            404,
            self.client.post_json("/server/migration/v1/status", {}, expect_errors=True).status_int,
        )

    def test_apply_requires_same_origin_token_and_exact_confirmation(self) -> None:
        payload = {
            "confirmation": "MIGRATE original-v0.8.6-to-current-v1",
            "retry": False,
        }
        self.assertEqual(403, self.client.post_json(
            "/server/migration/v1/apply", payload, expect_errors=True,
        ).status_int)
        self.assertEqual(403, self.client.post_json(
            "/server/migration/v1/apply", payload,
            headers={
                "Origin": "http://attacker.example",
                "X-SeedSync-Migration-CSRF": self.app._csrf_token,
            },
            expect_errors=True,
        ).status_int)
        self.assertEqual(403, self.client.post_json(
            "/server/migration/v1/apply", payload,
            headers={
                "Origin": "http://localhost:not-a-port",
                "X-SeedSync-Migration-CSRF": self.app._csrf_token,
            },
            expect_errors=True,
        ).status_int)
        self.assertEqual(409, self.client.post_json(
            "/server/migration/v1/apply",
            {"confirmation": "yes", "retry": False},
            headers={
                "Origin": "http://localhost",
                "X-SeedSync-Migration-CSRF": self.app._csrf_token,
            },
            expect_errors=True,
        ).status_int)
        self.coordinator.apply_confirmed.assert_not_called()

    def test_request_authority_rejects_dns_rebinding_before_status_or_apply(self) -> None:
        payload = {
            "confirmation": "MIGRATE original-v0.8.6-to-current-v1",
            "retry": False,
        }
        self.assertEqual(403, self.client.get(
            "/server/migration/v1/status",
            headers={"Host": "attacker.example"},
            expect_errors=True,
        ).status_int)
        self.assertEqual(403, self.client.get(
            "/migration", headers={"Host": "attacker.example"}, expect_errors=True,
        ).status_int)
        self.assertEqual(403, self.client.post_json(
            "/server/migration/v1/apply", payload,
            headers={
                "Host": "attacker.example",
                "Origin": "http://attacker.example",
                "X-SeedSync-Migration-CSRF": self.app._csrf_token,
            },
            expect_errors=True,
        ).status_int)
        self.coordinator.apply_confirmed.assert_not_called()

    def test_request_authority_accepts_local_private_and_exact_allowlisted_origins(self) -> None:
        for host in ("localhost", "127.0.0.1:8800", "192.168.50.20:8800", "[::1]:8800"):
            with self.subTest(host=host):
                self.assertEqual(200, self.client.get(
                    "/server/migration/v1/status", headers={"Host": host},
                ).status_int)
        for host in ("127.0.0.1:8800", "192.168.50.20:8800"):
            with self.subTest(apply_host=host):
                response = self.client.post_json(
                    "/server/migration/v1/apply",
                    {"confirmation": "wrong", "retry": False},
                    headers={
                        "Host": host, "Origin": "http://{}".format(host),
                        "X-SeedSync-Migration-CSRF": self.app._csrf_token,
                    },
                    expect_errors=True,
                )
                self.assertEqual(409, response.status_int)

        allowed = TestApp(MigrationWebApp(
            self.temp_dir.name, self.coordinator,
            allowed_origins=("http://seedsync.example",),
        ))
        self.assertEqual(200, allowed.get(
            "/server/migration/v1/status", headers={"Host": "seedsync.example"},
        ).status_int)
        response = allowed.post_json(
            "/server/migration/v1/apply",
            {"confirmation": "wrong", "retry": False},
            headers={
                "Host": "seedsync.example", "Origin": "http://seedsync.example",
                "X-SeedSync-Migration-CSRF": allowed.app._csrf_token,
            },
            expect_errors=True,
        )
        self.assertEqual(409, response.status_int)

    def test_request_authority_and_origin_fail_closed_on_malformed_or_proxy_mismatch(self) -> None:
        for host in (
            "user@localhost", "localhost, attacker.example", "localhost:not-a-port", "[::1",
        ):
            with self.subTest(host=host):
                self.assertEqual(403, self.client.get(
                    "/server/migration/v1/status", headers={"Host": host}, expect_errors=True,
                ).status_int)
        payload = {
            "confirmation": "MIGRATE original-v0.8.6-to-current-v1", "retry": False,
        }
        for origin in (
            "http://user:password@localhost", "http://localhost,http://attacker.example",
            "http://localhost/path", "http://localhost:not-a-port", "http://[::1",
        ):
            with self.subTest(origin=origin):
                self.assertEqual(403, self.client.post_json(
                    "/server/migration/v1/apply", payload,
                    headers={
                        "Host": "localhost", "Origin": origin,
                        "X-SeedSync-Migration-CSRF": self.app._csrf_token,
                    },
                    expect_errors=True,
                ).status_int)
        proxied = TestApp(MigrationWebApp(
            self.temp_dir.name, self.coordinator,
            allowed_origins=("https://seedsync.example",),
        ))
        self.assertEqual(403, proxied.get(
            "/server/migration/v1/status",
            headers={
                "Host": "seedsync.example",
                "X-Forwarded-Proto": "https",
            },
            expect_errors=True,
        ).status_int)

    def test_duplicate_normalized_allowed_origins_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate normalized origin"):
            MigrationWebApp(
                self.temp_dir.name, self.coordinator,
                allowed_origins=("http://seedsync.example", "http://SEEDSYNC.example:80"),
            )

    def test_running_status_polls_skip_retained_backup_revalidation(self) -> None:
        self.app._execution.status = "running"
        self.coordinator.retained_backup_ready.reset_mock()

        first = self.client.get("/server/migration/v1/status").json
        second = self.client.get("/server/migration/v1/status").json

        self.assertEqual("running", first["operation"]["status"])
        self.assertEqual("running", second["operation"]["status"])
        self.assertFalse(first["backup"]["complete_restore_ready"])
        self.assertFalse(second["backup"]["complete_restore_ready"])
        self.coordinator.retained_backup_ready.assert_not_called()

    def test_apply_requires_bounded_non_chunked_json_body(self) -> None:
        unlinted_client = TestApp(self.app, lint=False)
        headers = {
            "Host": "localhost",
            "Origin": "http://localhost",
            "X-SeedSync-Migration-CSRF": self.app._csrf_token,
            "Content-Type": "application/json",
        }
        valid = json.dumps({
            "confirmation": "MIGRATE original-v0.8.6-to-current-v1", "retry": False,
        }).encode("utf-8")
        cases = (
            ("missing-length", valid, {"CONTENT_LENGTH": ""}),
            ("malformed-length", valid, {"CONTENT_LENGTH": "not-a-number"}),
            ("zero-length", b"", {"CONTENT_LENGTH": "0"}),
            ("oversize", b"{}", {"CONTENT_LENGTH": "1025"}),
            ("conflicting-length", valid, {"CONTENT_LENGTH": "10, 10"}),
            ("declared-shorter", valid, {"CONTENT_LENGTH": str(len(valid) - 1)}),
            ("declared-longer", valid, {"CONTENT_LENGTH": str(len(valid) + 1)}),
        )
        for name, body, extra_environ in cases:
            with self.subTest(name=name):
                request = TestRequest.blank(
                    "/server/migration/v1/apply", method="POST", body=body, headers=headers,
                )
                request.environ.update(extra_environ)
                request.environ["wsgi.input"] = io.BytesIO(body)
                response = unlinted_client.do_request(request, expect_errors=True)
                self.assertEqual(400, response.status_int)
        self.assertEqual(400, self.client.post(
            "/server/migration/v1/apply", params=valid,
            headers={**headers, "Transfer-Encoding": "chunked"},
            expect_errors=True,
        ).status_int)
        self.assertEqual(400, self.client.post(
            "/server/migration/v1/apply", params=valid,
            headers={key: value for key, value in headers.items() if key != "Content-Type"},
            expect_errors=True,
        ).status_int)
        self.assertEqual(400, self.client.post(
            "/server/migration/v1/apply", params=valid,
            headers={**headers, "Content-Type": "text/plain"},
            expect_errors=True,
        ).status_int)
        for body in (b"not-json", b"{} trailing", b"[]"):
            with self.subTest(body=body):
                self.assertEqual(400, self.client.post(
                    "/server/migration/v1/apply", params=body, headers=headers,
                    expect_errors=True,
                ).status_int)
        accepted = self.client.post(
            "/server/migration/v1/apply", params=valid, headers=headers,
        )
        self.assertEqual(202, accepted.status_int)
        assert self.app._execution.worker is not None
        self.app._execution.worker.join(2)

    def test_apply_returns_202_through_real_wsgi_server_before_background_handoff(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def apply_confirmed(*, retry=False):
            self.assertFalse(retry)
            entered.set()
            release.wait(2)
            return MigrationDecision(MigrationState.COMPLETE, migration_id=self.decision.migration_id)

        self.coordinator.apply_confirmed.side_effect = apply_confirmed
        server = MyWSGIRefServer(logging.getLogger("migration-wsgi-test"), host="127.0.0.1", port=0)
        server_thread = threading.Thread(target=server.run, args=(self.app,), daemon=True)
        server_thread.start()
        try:
            deadline = time.monotonic() + 2
            while server.server is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(server.server)
            port = server.port
            body = json.dumps({
                "confirmation": "MIGRATE original-v0.8.6-to-current-v1", "retry": False,
            }).encode("utf-8")
            request = urllib.request.Request(
                "http://127.0.0.1:{}/server/migration/v1/apply".format(port),
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://127.0.0.1:{}".format(port),
                    "X-SeedSync-Migration-CSRF": self.app._csrf_token,
                },
            )

            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(202, response.status)
            self.assertTrue(entered.wait(1))
            self.assertEqual("running", payload["operation"]["status"])
        finally:
            release.set()
            server.stop()
            server_thread.join(2)

    def test_apply_is_background_single_flight_and_success_requests_normal_startup(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def apply_confirmed(*, retry=False):
            self.assertFalse(retry)
            entered.set()
            release.wait(2)
            return MigrationDecision(
                MigrationState.COMPLETE,
                migration_id=self.decision.migration_id,
            )

        self.coordinator.apply_confirmed.side_effect = apply_confirmed
        headers = {
            "Origin": "http://localhost",
            "X-SeedSync-Migration-CSRF": self.app._csrf_token,
        }
        payload = {
            "confirmation": "MIGRATE original-v0.8.6-to-current-v1",
            "retry": False,
        }
        response = self.client.post_json(
            "/server/migration/v1/apply", payload, headers=headers,
        )
        self.assertEqual(202, response.status_int)
        self.assertTrue(entered.wait(1))
        self.assertEqual("running", response.json["operation"]["status"])
        self.assertEqual(409, self.client.post_json(
            "/server/migration/v1/apply", payload, headers=headers, expect_errors=True,
        ).status_int)
        release.set()
        assert self.app._execution.worker is not None
        self.app._execution.worker.join(2)
        self.assertFalse(self.app._execution.worker.is_alive())
        self.on_success.assert_called_once_with()

    def test_background_apply_failure_records_safe_diagnostic_without_request_values(self) -> None:
        supplied_token = "synthetic-csrf-token"
        supplied_confirmation = "MIGRATE original-v0.8.6-to-current-v1"
        self.app._csrf_token = supplied_token
        def raise_apply_error(*, retry=False):
            self.assertFalse(retry)
            raise RuntimeError("csrf={} confirmation={}".format(supplied_token, supplied_confirmation))
        self.coordinator.apply_confirmed.side_effect = raise_apply_error
        headers = {
            "Origin": "http://localhost",
            "X-SeedSync-Migration-CSRF": supplied_token,
        }
        payload = {"confirmation": supplied_confirmation, "retry": False}

        with self.assertLogs("SeedSync.MigrationWeb", level="ERROR") as logs:
            response = self.client.post_json("/server/migration/v1/apply", payload, headers=headers)
            self.assertEqual(202, response.status_int)
            assert self.app._execution.worker is not None
            self.app._execution.worker.join(2)

        self.assertEqual("failed", self.app._execution.status)
        diagnostic = "\n".join(logs.output)
        self.assertIn("type=RuntimeError", diagnostic)
        self.assertIn("location=test_migration_web_app.py:", diagnostic)
        self.assertNotIn(supplied_token, diagnostic)
        self.assertNotIn(supplied_confirmation, diagnostic)
        self.assertEqual("failed", self.client.get("/server/migration/v1/status").json["operation"]["status"])

    def test_background_apply_failure_sanitizes_log_metadata_controls(self) -> None:
        unsafe_error = type("Unsafe\nType", (Exception,), {})

        def raise_apply_error(*, retry=False):
            self.assertFalse(retry)
            raise unsafe_error("request-value")

        self.coordinator.apply_confirmed.side_effect = raise_apply_error
        with self.assertLogs("SeedSync.MigrationWeb", level="ERROR") as logs:
            response = self.client.post_json(
                "/server/migration/v1/apply",
                {"confirmation": "MIGRATE original-v0.8.6-to-current-v1", "retry": False},
                headers={"Origin": "http://localhost", "X-SeedSync-Migration-CSRF": self.app._csrf_token},
            )
            self.assertEqual(202, response.status_int)
            assert self.app._execution.worker is not None
            self.app._execution.worker.join(2)

        diagnostic = "\n".join(logs.output)
        self.assertIn("type=Unsafe_Type", diagnostic)
        self.assertNotIn("Unsafe\nType", diagnostic)
        self.assertNotIn("request-value", diagnostic)

    def test_retry_action_is_available_only_for_retryable_failed_state(self) -> None:
        failed = MigrationDecision(
            MigrationState.FAILED,
            migration_id=self.decision.migration_id,
            source_schema=self.decision.source_schema,
            retryable=True,
        )
        self.coordinator.status.return_value = failed
        self.coordinator.apply_confirmed.return_value = failed
        headers = {
            "Origin": "http://localhost",
            "X-SeedSync-Migration-CSRF": self.app._csrf_token,
        }
        response = self.client.post_json(
            "/server/migration/v1/apply",
            {
                "confirmation": "MIGRATE original-v0.8.6-to-current-v1",
                "retry": True,
            },
            headers=headers,
        )
        self.assertEqual(202, response.status_int)
        assert self.app._execution.worker is not None
        self.app._execution.worker.join(2)
        self.coordinator.apply_confirmed.assert_called_once_with(retry=True)
        self.on_success.assert_not_called()

    def test_runtime_uses_supplied_bind_host_and_legacy_port(self) -> None:
        runtime = MigrationWebRuntime(
            bind_host="127.0.0.1", port=9876,
            html_path=self.temp_dir.name, coordinator=self.coordinator,
        )
        server = MagicMock()
        with patch("web.migration_web_app.MyWSGIRefServer", return_value=server) as server_type, \
             patch("web.migration_web_app.bottle.run"):
            runtime.run()
        server_type.assert_called_once_with(runtime._logger, host="127.0.0.1", port=9876)

    def test_runtime_success_schedules_controlled_return_to_startup_loop(self) -> None:
        runtime = MigrationWebRuntime(
            bind_host="127.0.0.1", port=9876,
            html_path=self.temp_dir.name, coordinator=self.coordinator,
        )
        timer = MagicMock()
        with patch("web.migration_web_app.threading.Timer", return_value=timer) as timer_type:
            runtime._schedule_normal_startup()
            runtime._schedule_normal_startup()

        timer_type.assert_called_once_with(2.0, runtime.stop)
        self.assertTrue(timer.daemon)
        timer.start.assert_called_once_with()

    def test_repeated_status_gets_do_not_change_required_failed_or_stale_config(self) -> None:
        fixture_root = Path(__file__).parents[2] / "fixtures" / "upgrade_v086_ff2a"
        cases: list[tuple[str, Path]] = []

        required_root = Path(self.temp_dir.name) / "required-config"
        required_root.mkdir()
        for name in ("settings.cfg", "controller.persist", "autoqueue.persist"):
            shutil.copyfile(fixture_root / name, required_root / name)
        cases.append(("required", required_root))

        failed_root = Path(self.temp_dir.name) / "failed-config"
        failed_root.mkdir()
        (failed_root / "settings.cfg").write_text("[General]\nverbose=True\n", encoding="utf-8")
        cases.append(("failed", failed_root))

        stale_root = Path(self.temp_dir.name) / "stale-config"
        shutil.copytree(required_root, stale_root)
        required = MigrationCoordinator(stale_root).preflight()
        (stale_root / "migration-state.json").write_text(json.dumps({
            "metadata_version": 2,
            "state": "running",
            "migration_id": required.migration_id,
            "source_schema": required.source_schema,
            "target_schema": required.target_schema,
            "current_schema": required.source_schema,
            "applied_migrations": [],
            "attempt": 1,
            "error": None,
            "retryable": False,
        }), encoding="utf-8")
        cases.append(("stale", stale_root))

        for name, config_root in cases:
            with self.subTest(name=name):
                before = {
                    str(path.relative_to(config_root)): path.read_bytes()
                    for path in config_root.rglob("*") if path.is_file()
                }
                client = TestApp(MigrationWebApp(
                    self.temp_dir.name, MigrationCoordinator(config_root),
                ))
                client.get("/server/migration/v1/status")
                client.get("/server/migration/v1/status")
                after = {
                    str(path.relative_to(config_root)): path.read_bytes()
                    for path in config_root.rglob("*") if path.is_file()
                }
                self.assertEqual(before, after)

    def test_status_keeps_runtime_augmented_v086_actionable_without_creating_backup(self) -> None:
        fixture_root = Path(__file__).parents[2] / "fixtures" / "upgrade_v086_ff2a"
        config_root = Path(self.temp_dir.name) / "runtime-augmented-v086"
        config_root.mkdir()
        for name in ("settings.cfg", "controller.persist", "autoqueue.persist"):
            shutil.copyfile(fixture_root / name, config_root / name)
        settings = (config_root / "settings.cfg").read_text(encoding="utf-8")
        settings = settings.replace(
            "[General]\n",
            "[General]\n"
            "trusted_browser_bootstrap_remote_addrs = 172.17.0.1\n"
            "config_api_redact_remote_details = False\n",
        )
        (config_root / "settings.cfg").write_text(settings, encoding="utf-8")
        before = {
            str(path.relative_to(config_root)): path.read_bytes()
            for path in config_root.rglob("*") if path.is_file()
        }
        client = TestApp(MigrationWebApp(
            self.temp_dir.name, MigrationCoordinator(config_root),
        ))

        first = client.get("/server/migration/v1/status").json
        second = client.get("/server/migration/v1/status").json

        for payload in (first, second):
            self.assertEqual("required", payload["state"])
            self.assertEqual("original-v0.8.6-to-current-v1", payload["migration_id"])
            self.assertEqual("original-v0.8.6", payload["source_schema"])
            self.assertEqual(
                {"apply": True, "retry": False, "restore": False},
                payload["capabilities"],
            )
            self.assertFalse(payload["backup"]["complete_restore_ready"])
        after = {
            str(path.relative_to(config_root)): path.read_bytes()
            for path in config_root.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertFalse((config_root / "migration-backups").exists())
        self.assertFalse((config_root / "migration-state.json").exists())

    def test_status_revalidates_retained_backup_after_prior_ready_response(self) -> None:
        fixture_root = Path(__file__).parents[2] / "fixtures" / "upgrade_v086_ff2a"
        config_root = Path(self.temp_dir.name) / "revalidated-backup"
        config_root.mkdir()
        for name in ("settings.cfg", "controller.persist", "autoqueue.persist"):
            shutil.copyfile(fixture_root / name, config_root / name)
        coordinator = MigrationCoordinator(config_root)
        coordinator.apply_confirmed()
        client = TestApp(MigrationWebApp(self.temp_dir.name, coordinator))
        first = client.get("/server/migration/v1/status").json
        self.assertTrue(first["backup"]["complete_restore_ready"])
        metadata = json.loads((config_root / "migration-state.json").read_text(encoding="utf-8"))
        backup_settings = config_root / metadata["backup"] / "data" / "settings.cfg"

        backup_settings.write_bytes(backup_settings.read_bytes() + b"\ncorrupted=True\n")

        second = client.get("/server/migration/v1/status").json
        self.assertFalse(second["backup"]["complete_restore_ready"])


if __name__ == "__main__":
    unittest.main()
