# Copyright 2026, SeedSync Contributors, All rights reserved.

"""Maintained local composition coverage for the Incoming recovery route.

The transfer backend is deliberately local.  The test still runs the real web
controller handler, command callback boundary, filesystem scanner, sidecar
parser and LFTP status/accounting types.  SSH/SFTP and an actual LFTP process
remain outside this deterministic test's boundary.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import tempfile
import unittest

from webtest import TestApp

from common import Args, BreadcrumbTraceCollector, Config, Status
from common.lftp_status import parse_lftp_pget_status
from controller import AutoQueuePersist, Controller
from lftp import LftpJobStatus
from system import SystemScanner
from web import WebAppBuilder
from web.auth_store import ApiKeyStore


class _LocalIncomingController:
    """Small local backend that preserves the application boundary under test."""

    _TOTAL_SIZE = 12
    _PARTIAL_POINTS = (4, 8)

    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote_root = root / "remote"
        self.final_root = root / "final"
        self.staging_root = root / "incoming"
        for directory in (self.remote_root, self.final_root, self.staging_root):
            directory.mkdir(parents=True)

        self.resume_source = self.remote_root / "resume.bin"
        self.collision_source = self.remote_root / "collision.bin"
        self.resume_final = self.final_root / "resume.bin"
        self.collision_final = self.final_root / "collision.bin"
        self.collision_staging = self.staging_root / "collision.bin"
        self.protected_final = self.final_root / "protected.bin"
        self.resume_source.write_bytes(b"resume-data!")
        self.collision_source.write_bytes(b"remote-collision")
        self.protected_final.write_bytes(b"protected-final-content")
        self.collision_final.write_bytes(b"existing-final-content")
        self.collision_staging.write_bytes(self.collision_source.read_bytes()[:5])
        (self.collision_staging.with_name("collision.bin.lftp-pget-status")).write_text(
            "size={}\n0.pos=5\n0.limit={}\n".format(
                self.collision_source.stat().st_size,
                self.collision_source.stat().st_size,
            ),
            encoding="utf-8",
        )

        self.commands: list[tuple[Controller.Command.Action, str]] = []
        self.queue_count = 0
        self.scanner_generations: list[int] = []
        self.scanner_handoffs: list[tuple[int, int, int, bool]] = []
        self.status_history: list[LftpJobStatus] = []
        self.accounting: list[tuple[int, int, int]] = []
        self.owner = "idle"
        self.stop_count = 0
        self.http_waits: list[tuple[str, bool, bool | None]] = []

    def get_model_file_command_identities(self) -> tuple[tuple[str, str, str | None], ...]:
        return (
            ("resume.bin", "resume.bin", None),
            ("collision.bin", "collision.bin", None),
        )

    def request_lftp_reconfigure(self) -> None:
        pass

    def record_queue_http_wait_trace(
            self, file_id: str, completed: bool, success: bool | None,
    ) -> None:
        self.http_waits.append((file_id, completed, success))

    def queue_command(self, command: Controller.Command) -> None:
        self.commands.append((command.action, command.filename))
        if command.action == Controller.Command.Action.STOP:
            self._stop(command)
            return
        if command.action != Controller.Command.Action.QUEUE:
            self._fail(command, "Unsupported local recovery action", 400)
            return
        self.queue_count += 1
        if command.filename == "collision.bin":
            # The destination is protected by an existing Final leaf.  The
            # collision is intentionally terminal and never touches staging.
            collision_staging = SystemScanner(str(self.staging_root)).scan_single("collision.bin")
            if not collision_staging.status_sidecar_ready:
                self._fail(command, "Collision sidecar is not valid", 500)
                return
            self._fail(command, "Queue collision is still present", 409)
            return
        if command.filename != "resume.bin":
            self._fail(command, "Unknown local recovery file", 404)
            return
        self._queue_resume(command)

    def _fail(self, command: Controller.Command, message: str, code: int) -> None:
        for callback in command.callbacks:
            callback.on_failure(message, code)

    def _stop(self, command: Controller.Command) -> None:
        if command.filename != "resume.bin" or self.owner != "running":
            self._fail(command, "File is not running", 409)
            return
        self.owner = "stopped"
        self.stop_count += 1
        for callback in command.callbacks:
            callback.on_success()

    def _queue_resume(self, command: Controller.Command) -> None:
        self.owner = "running"
        self.scanner_generations.append(len(self.scanner_generations) + 1)
        remote_scanned = SystemScanner(str(self.remote_root)).scan_single("resume.bin")
        self._assert_remote_scan(remote_scanned.size)

        staging_target = self.staging_root / "resume.bin"
        sidecar_path = Path(str(staging_target) + ".lftp-pget-status")
        existing_coverage = 0
        if sidecar_path.exists():
            parsed = parse_lftp_pget_status(sidecar_path.read_text(encoding="utf-8"))
            if parsed is None:
                self._fail(command, "Resume sidecar is not valid", 409)
                self.owner = "stopped"
                return
            existing_coverage = parsed.covered_size

        # The first admission models a bounded interrupted transfer.  A later
        # Queue request resumes the same sidecar and publishes the final leaf.
        if existing_coverage == 0:
            for covered in self._PARTIAL_POINTS:
                self._publish_checkpoint(staging_target, sidecar_path, covered)
        else:
            self._publish_checkpoint(staging_target, sidecar_path, self._TOTAL_SIZE)
            if self.resume_final.exists():
                self._fail(command, "Queue collision is still present", 409)
                self.owner = "stopped"
                return
            os.replace(staging_target, self.resume_final)
            sidecar_path.unlink()
            self.owner = "complete"

        for callback in command.callbacks:
            callback.on_success()

    def _publish_checkpoint(self, target: Path, sidecar: Path, covered: int) -> None:
        payload = self.resume_source.read_bytes()
        with target.open("wb") as handle:
            # Keep the allocated/raw file size at the declared total.  The
            # scanner must obtain progress from the validated sidecar instead.
            handle.truncate(self._TOTAL_SIZE)
            handle.seek(0)
            handle.write(payload[:covered])
        sidecar.write_text(
            "size={}\n0.pos={}\n0.limit={}\n".format(self._TOTAL_SIZE, covered, self._TOTAL_SIZE),
            encoding="utf-8",
        )
        parsed = parse_lftp_pget_status(sidecar.read_text(encoding="utf-8"))
        assert parsed is not None
        scanned = SystemScanner(str(self.staging_root)).scan_single("resume.bin")
        status = LftpJobStatus(
            len(self.status_history),
            LftpJobStatus.Type.PGET,
            LftpJobStatus.State.RUNNING,
            "resume.bin",
            "",
        )
        status.total_transfer_state = LftpJobStatus.TransferState(
            parsed.covered_size,
            parsed.total_size,
            (parsed.covered_size * 100) // parsed.total_size,
            1,
            parsed.total_size - parsed.covered_size,
        )
        self.status_history.append(status)
        self.accounting.append(
            (parsed.covered_size, status.total_transfer_state.size_local, scanned.size),
        )
        assert scanned.status_sidecar_ready
        self.scanner_handoffs.append((
            self.scanner_generations[-1],
            self._TOTAL_SIZE,
            scanned.size,
            scanned.status_sidecar_ready,
        ))

    def _assert_remote_scan(self, size: int) -> None:
        if size != self._TOTAL_SIZE:
            raise AssertionError("remote scanner did not publish the expected source size")


class TestIncomingRecoveryComposed(unittest.TestCase):
    """Exercise the local S3 route with preservation, collision and resume."""

    def test_http_queue_scanner_status_collision_and_resume(self) -> None:
        with tempfile.TemporaryDirectory(prefix="incoming-recovery-composed-") as temp_dir:
            root = Path(temp_dir)
            controller = _LocalIncomingController(root)
            protected_before = hashlib.sha256(controller.protected_final.read_bytes()).digest()
            collision_before = controller.collision_final.read_bytes()
            collision_staging_before = controller.collision_staging.read_bytes()
            collision_sidecar = controller.collision_staging.with_name("collision.bin.lftp-pget-status")
            collision_sidecar_before = collision_sidecar.read_bytes()

            context = type("Context", (), {})()
            context.logger = logging.getLogger("incoming-recovery-composed")
            context.config = Config()
            context.config.lftp.local_path = str(controller.final_root)
            context.args = Args()
            context.args.history_log_path = None
            context.status = Status()
            context.breadcrumb_trace = BreadcrumbTraceCollector(lambda: False)
            context.path_pair_manager = None
            auth_store = ApiKeyStore(file_path=str(root / "api-keys.json"))
            token = auth_store.create_api_key("incoming-recovery-test", ["admin"])["secret"]
            app = TestApp(
                WebAppBuilder(context, controller, AutoQueuePersist(), auth_store).build(),
                extra_environ={"HTTP_AUTHORIZATION": "Bearer {}".format(token)},
            )

            first = app.post("/server/command/queue/resume.bin")
            self.assertEqual(200, first.status_int)
            self.assertEqual("running", controller.owner)
            self.assertEqual(1, controller.queue_count)
            self.assertEqual((4, 8), tuple(row[0] for row in controller.accounting))
            self.assertEqual((1, 1), tuple(row[0] for row in controller.scanner_handoffs))
            self.assertEqual((4, 8), tuple(row[2] for row in controller.scanner_handoffs))
            self.assertTrue(all(row[3] for row in controller.scanner_handoffs))

            stop = app.post("/server/command/stop/resume.bin")
            self.assertEqual(200, stop.status_int)
            self.assertEqual("stopped", controller.owner)
            self.assertEqual(1, controller.stop_count)
            self.assertTrue((controller.staging_root / "resume.bin.lftp-pget-status").exists())

            resumed = app.post("/server/command/queue/resume.bin")
            self.assertEqual(200, resumed.status_int)
            self.assertEqual("complete", controller.owner)
            self.assertEqual(2, controller.queue_count)
            self.assertEqual((4, 4), (
                controller.accounting[1][0] - controller.accounting[0][0],
                controller.accounting[2][0] - controller.accounting[1][0],
            ))
            self.assertEqual((1, 1, 2), tuple(row[0] for row in controller.scanner_handoffs))
            self.assertEqual((4, 8, 12), tuple(row[2] for row in controller.scanner_handoffs))
            self.assertEqual(
                (4, 8, 12),
                tuple(status.total_transfer_state.size_local for status in controller.status_history),
            )
            self.assertEqual(controller.resume_source.read_bytes(), controller.resume_final.read_bytes())
            self.assertFalse((controller.staging_root / "resume.bin").exists())
            self.assertFalse((controller.staging_root / "resume.bin.lftp-pget-status").exists())

            collision = app.post("/server/command/queue/collision.bin", expect_errors=True)
            self.assertEqual(409, collision.status_int)
            self.assertEqual("complete", controller.owner)
            self.assertEqual(collision_before, controller.collision_final.read_bytes())
            self.assertEqual(collision_staging_before, controller.collision_staging.read_bytes())
            self.assertEqual(collision_sidecar_before, collision_sidecar.read_bytes())
            self.assertEqual(protected_before, hashlib.sha256(controller.protected_final.read_bytes()).digest())
            self.assertEqual(
                [
                    (Controller.Command.Action.QUEUE, "resume.bin"),
                    (Controller.Command.Action.STOP, "resume.bin"),
                    (Controller.Command.Action.QUEUE, "resume.bin"),
                    (Controller.Command.Action.QUEUE, "collision.bin"),
                ],
                controller.commands,
            )
            self.assertEqual(
                [("resume.bin", True, True), ("resume.bin", True, True),
                 ("collision.bin", True, False)],
                controller.http_waits,
            )


if __name__ == "__main__":
    unittest.main()
