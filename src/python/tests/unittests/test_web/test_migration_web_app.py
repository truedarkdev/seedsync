import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from migration import MigrationCoordinator, MigrationDecision, MigrationFeature, MigrationState
from web.migration_web_app import MigrationWebApp, MigrationWebRuntime, migration_status_payload
from webtest import TestApp


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
        self.client = TestApp(MigrationWebApp(str(html_root), self.coordinator))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_status_exposes_versioned_read_only_contract(self) -> None:
        response = self.client.get("/server/migration/v1/status")

        self.assertEqual(200, response.status_int)
        self.assertEqual(migration_status_payload(self.decision), json.loads(response.text))
        self.assertEqual(
            {"apply": False, "retry": False, "restore": False},
            response.json["capabilities"],
        )
        self.assertEqual(
            {"required": True, "complete_restore_ready": False, "status": "not_ready"},
            response.json["backup"],
        )
        self.assertEqual("complete_backup_restore_not_ready", response.json["blocker"])
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
            error=r"failed while reading C:\Users\person\secret\settings.cfg",
            retryable=True,
        )

        response = self.client.get("/server/migration/v1/status")

        self.assertEqual(
            {
                "code": "migration_preflight_failed",
                "message": "SeedSync could not complete the migration readiness check.",
            },
            response.json["error"],
        )
        self.assertNotIn("C:\\Users", response.text)
        self.assertTrue(response.json["retryable"])
        self.assertFalse(response.json["capabilities"]["retry"])

    def test_defensive_states_remain_read_only(self) -> None:
        for state in (MigrationState.RUNNING, MigrationState.FAILED, MigrationState.COMPLETE):
            with self.subTest(state=state.value):
                self.coordinator.status.return_value = MigrationDecision(state=state, retryable=True)
                payload = self.client.get("/server/migration/v1/status").json
                self.assertEqual(state.value, payload["state"])
                self.assertEqual({"apply": False, "retry": False, "restore": False}, payload["capabilities"])

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
            "/server/migration/v1/apply",
            "/settings",
            "/bootstrap",
        ):
            with self.subTest(path=path):
                self.assertEqual(404, self.client.get(path, expect_errors=True).status_int)

        self.assertEqual(
            404,
            self.client.post_json("/server/migration/v1/apply", {}, expect_errors=True).status_int,
        )
        self.assertEqual(
            404,
            self.client.post_json("/server/migration/v1/status", {}, expect_errors=True).status_int,
        )

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


if __name__ == "__main__":
    unittest.main()
