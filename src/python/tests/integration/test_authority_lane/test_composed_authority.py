"""Bounded composed discriminator for Queue scan authority handoff.

This lane deliberately keeps the real Controller, ModelUpdater, and
ScannerProcess objects while replacing only the external transfer backend and
scanner inputs with deterministic local fixtures.  It is diagnostic coverage,
not application behavior coverage.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import unittest
from pathlib import Path

from common import Args, BreadcrumbTraceCollector, Config, Context, PathPair, PathPairManager, Status
from controller import Controller, ControllerPersist
from controller.scan import IScanner, ScannerProcess
from model import ModelFile
from system import SystemFile


class _RecordingTransferBackend:
    """Synchronous backend seam: records dispatch, never performs transfer."""

    backend_name = "authority-lane"
    last_status_poll_healthy = True

    def __init__(self) -> None:
        self.queue_calls: list[tuple[object, ...]] = []
        self.status_calls = 0

    def set_base_logger(self, _logger: object) -> None:
        pass

    def set_breadcrumb_trace(self, _trace: object) -> None:
        pass

    def set_base_remote_dir_path(self, _path: str) -> None:
        pass

    def set_base_local_dir_path(self, _path: str) -> None:
        pass

    def set_path_pairs(self, _path_pairs: object) -> None:
        pass

    def set_verbose_logging(self, _enabled: bool) -> None:
        pass

    def status(self) -> list[object]:
        self.status_calls += 1
        return []

    def queue(self, *args: object, **kwargs: object) -> bool:
        self.queue_calls.append((args, kwargs))
        return True

    def raise_pending_error(self) -> None:
        pass

    def exit(self) -> None:
        pass


class _SequencedScanner(IScanner):
    """Return one deterministic snapshot, optionally gated before first scan."""

    def __init__(
            self, files: list[SystemFile], first_scan_gate: threading.Event | None = None,
            path_pair_id: str | None = None,
    ) -> None:
        self._files = files
        self._first_scan_gate = first_scan_gate
        self._path_pair_id = path_pair_id
        self.first_scan_started = threading.Event()
        self.first_scan_returned = threading.Event()
        self._scan_count = 0

    def set_base_logger(self, _base_logger: logging.Logger) -> None:
        pass

    def scan(self) -> list[SystemFile]:
        self._scan_count += 1
        if self._scan_count == 1:
            self.first_scan_started.set()
            if self._first_scan_gate is not None:
                self._first_scan_gate.wait(timeout=3.0)
        result = copy.deepcopy(self._files)
        self.first_scan_returned.set()
        # Avoid flooding the bounded scanner queue while the controller is
        # intentionally paused at the authority boundary.
        time.sleep(0.01)
        return result

    def scanned_path_pair_ids(self) -> set[str | None]:
        return {self._path_pair_id}

    def failed_path_pair_ids(self) -> set[str | None]:
        return set()


class _Callback(Controller.Command.ICallback):
    def __init__(self) -> None:
        self.success_count = 0
        self.failures: list[tuple[str, int]] = []

    def on_success(self) -> None:
        self.success_count += 1

    def on_failure(self, error: str, error_code: int = 400) -> None:
        self.failures.append((error, error_code))


def _directory_fixture(
        name: str = "queue-root", path_pair_id: str | None = None,
        path_pair_name: str | None = None,
) -> list[SystemFile]:
    root = SystemFile(name, 10, is_dir=True)
    root.add_child(SystemFile("payload.bin", 10))
    root.path_pair_id = path_pair_id
    root.path_pair_name = path_pair_name
    for child in root.iter_children():
        child.path_pair_id = path_pair_id
        child.path_pair_name = path_pair_name
    return [root]


class TestConfiguredPairAuthorityComposition(unittest.TestCase):
    """Prove configured-pair authority deferral across the real authority path."""

    def test_queue_defers_until_pair_authority_then_dispatches_once(self) -> None:
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="authority-lane-") as temp_dir:
            root = Path(temp_dir)
            remote_root = root / "remote"
            local_root = root / "local"
            remote_root.mkdir()
            local_root.mkdir()
            pair_id = "pair-a"
            pair_name = "Pair A"

            config = Config.from_dict({
                "General": {"debug": "False", "verbose": "False"},
                "Lftp": {
                    "remote_address": "authority-lane.invalid",
                    "remote_username": "user",
                    "remote_password": "password",
                    "remote_port": "22",
                    "remote_path": str(remote_root),
                    "local_path": str(local_root),
                    "remote_path_to_scan_script": "unused",
                    "use_ssh_key": "False",
                    "protocol": "sftp",
                    "num_max_parallel_downloads": "1",
                    "num_max_parallel_files_per_download": "1",
                    "num_max_connections_per_root_file": "1",
                    "num_max_connections_per_dir_file": "1",
                    "num_max_total_connections": "1",
                    "use_temp_file": "False",
                    "rate_limit": "0",
                    "net_socket_buffer": "512K",
                },
                "Controller": {
                    "interval_ms_remote_scan": "1",
                    "interval_ms_local_scan": "1",
                    "interval_ms_downloading_scan": "1",
                    "extract_path": str(local_root),
                    "use_local_path_as_extract_path": "True",
                    "managed_extract_folders_enabled": "True",
                },
                "Web": {"port": "8800"},
                "AutoQueue": {
                    "enabled": "False",
                    "patterns_only": "False",
                    "auto_extract": "False",
                },
            })
            args = Args()
            args.local_path_to_scanfs = "unused"
            logger = logging.getLogger("authority-lane")
            path_pair_manager = PathPairManager(str(root / "config"))
            path_pair_manager.load()
            path_pair_manager.add_pair(PathPair(
                id=pair_id,
                name=pair_name,
                remote_path=str(remote_root),
                local_path=str(local_root),
                enabled=True,
                auto_queue=True,
            ))
            context = Context(
                logger=logger,
                web_access_logger=logger,
                config=config,
                args=args,
                status=Status(),
                path_pair_manager=path_pair_manager,
            )
            context.breadcrumb_trace = BreadcrumbTraceCollector(
                lambda: True,
                policy={
                    "default": "off",
                    "rules": {
                        "queue.readiness": "info",
                        "queue.authority": "info",
                        "scan.result": "info",
                    },
                },
                max_entries=256,
            )

            backend = _RecordingTransferBackend()
            controller: Controller | None = None
            release_local = threading.Event()
            local_scanner = _SequencedScanner([], first_scan_gate=release_local, path_pair_id=pair_id)
            remote_scanner = _SequencedScanner(
                _directory_fixture(path_pair_id=pair_id, path_pair_name=pair_name),
                path_pair_id=pair_id,
            )
            active_scanner = _SequencedScanner([], path_pair_id=pair_id)
            try:
                with patch("controller.controller.create_transfer_backend", return_value=backend):
                    controller = Controller(context, ControllerPersist())

                def process(scanner: IScanner) -> ScannerProcess:
                    return ScannerProcess(
                        scanner=scanner,
                        interval_in_ms=1,
                        verbose=False,
                        breadcrumb_trace=context.breadcrumb_trace.create_emitter(),
                        recycle_scan_worker=False,
                        result_available_callback=controller.wake_process,
                    )

                # Keep the coordinator and updater real; only the external
                # scan inputs and transfer side effect are deterministic.
                controller._Controller__active_scan_process = process(active_scanner)
                controller._Controller__local_scan_process = process(local_scanner)
                controller._Controller__remote_scan_process = process(remote_scanner)

                reconciliation_events: list[
                    tuple[set[str | None] | None, set[str | None] | None]
                ] = []
                original_record_reconciliation = controller._record_path_pair_reconciliation

                def record_reconciliation(
                        local_ids: set[str | None] | None,
                        remote_ids: set[str | None] | None,
                ) -> None:
                    reconciliation_events.append((
                        None if local_ids is None else set(local_ids),
                        None if remote_ids is None else set(remote_ids),
                    ))
                    original_record_reconciliation(local_ids, remote_ids)

                controller._record_path_pair_reconciliation = record_reconciliation
                controller.start()

                self.assertTrue(local_scanner.first_scan_started.wait(1.0))
                self.assertTrue(remote_scanner.first_scan_returned.wait(1.0))

                # Consume the remote-only publication while the local side is
                # still gated.  The configured pair must remain fenced at this
                # authority boundary.
                model_seen = False
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    controller.process()
                    file_id = ModelFile.build_file_id("queue-root", pair_id)
                    model_seen = any(
                        file.file_id == file_id
                        for file in controller.get_model_files()
                    )
                    if model_seen:
                        break
                    time.sleep(0.005)
                self.assertTrue(model_seen, "remote publication did not reach the real ModelUpdater")

                callback = _Callback()
                command = Controller.Command(
                    Controller.Command.Action.QUEUE,
                    file_id,
                )
                command.add_callback(callback)
                controller.queue_command(command)
                controller.process()

                first_local_assignment = next(
                    (
                        event for event in reconciliation_events
                        if event[0] is not None and pair_id in event[0]
                    ),
                    None,
                )

                # A configured pair must remain fenced until its local
                # inventory result supplies the first local authority.
                self.assertGreaterEqual(len(reconciliation_events), 2)
                self.assertEqual(({None}, None), reconciliation_events[0])
                self.assertEqual((None, {pair_id}), reconciliation_events[1])
                self.assertIsNone(first_local_assignment)
                self.assertEqual([], backend.queue_calls)
                self.assertFalse(local_scanner.first_scan_returned.is_set())
                self.assertEqual(0, callback.success_count)
                self.assertEqual([], callback.failures)
                self.assertIn(file_id, controller._Controller__deferred_queue_intents)

                entries = context.breadcrumb_trace.snapshot()["entries"]
                scan_results = [
                    entry["message"] for entry in entries
                    if entry.get("category") == "scan.result"
                ]
                self.assertIn("remote_scan_result", scan_results)
                self.assertNotIn("local_scan_result", scan_results)
                readiness = [
                    entry for entry in entries
                    if entry.get("category") in {"queue.readiness", "queue.authority"}
                ]
                messages = [entry["message"] for entry in readiness]
                self.assertIn("queue_rescan_readiness", messages)
                self.assertIn("queue_final_decision", messages)
                self.assertNotIn("queue_callback", messages)
                final_decision = next(
                    entry for entry in readiness
                    if entry["message"] == "queue_final_decision"
                )
                self.assertEqual("deferred", final_decision["details"]["outcome"])
                self.assertEqual(
                    "initial_scan_authority_pending",
                    final_decision["details"]["reason"],
                )
                self.assertFalse(final_decision["details"]["local_reconciled"])
                self.assertTrue(final_decision["details"]["remote_reconciled"])
                self.assertTrue(final_decision["details"]["path_pair_scope_configured"])
                self.assertFalse(final_decision["details"]["dispatch_attempted"])
                reconciliation_count_before_release = len(reconciliation_events)
                release_local.set()
                self.assertTrue(local_scanner.first_scan_returned.wait(1.0))
                settlement_deadline = time.monotonic() + 2.0
                while time.monotonic() < settlement_deadline:
                    controller.process()
                    if backend.queue_calls and callback.success_count:
                        break
                    time.sleep(0.005)

                post_release_local_assignments = [
                    event for event in reconciliation_events[reconciliation_count_before_release:]
                    if event[0] is not None and pair_id in event[0]
                ]
                self.assertTrue(post_release_local_assignments)
                self.assertEqual(1, len(backend.queue_calls))
                self.assertEqual(1, callback.success_count)
                self.assertEqual([], callback.failures)
                self.assertNotIn(file_id, controller._Controller__deferred_queue_intents)

                # Extra controller ticks must not replay the deferred command
                # or invoke its callback a second time.
                for _ in range(25):
                    controller.process()
                    time.sleep(0.005)
                self.assertEqual(1, len(backend.queue_calls))
                self.assertEqual(1, callback.success_count)
                entries_after_settlement = context.breadcrumb_trace.snapshot()["entries"]
                self.assertEqual(1, sum(
                    entry.get("message") == "queue_callback"
                    for entry in entries_after_settlement
                ))
            finally:
                release_local.set()
                if controller is not None:
                    controller.exit()


if __name__ == "__main__":
    unittest.main()
