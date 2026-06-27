# Copyright 2026, SeedSync Contributors, All rights reserved.

import os
import tempfile
import unittest
from unittest.mock import patch

from transfer.rclone_backend import RcloneTransferBackend, RcloneTransferError


class _FakePopen:
    instances = []

    def __init__(self, command, stdout=None, stderr=None, text=None):
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.text = text
        self._returncode = None
        self.terminated = False
        self.killed = False
        _FakePopen.instances.append(self)

    def poll(self):
        return self._returncode

    @property
    def returncode(self):
        return self._returncode

    def communicate(self):
        return ("", "")

    def terminate(self):
        self.terminated = True
        self._returncode = -15

    def kill(self):
        self.killed = True
        self._returncode = -9


class _CompletedProcess:
    def __init__(self, stdout="obscured-password\n", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestRcloneTransferBackend(unittest.TestCase):
    def setUp(self):
        _FakePopen.instances = []

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    @patch("transfer.rclone_backend.subprocess.Popen", side_effect=_FakePopen)
    def test_queue_starts_running_job_and_reports_status(self, _mock_popen, _mock_run, _mock_which):
        with tempfile.TemporaryDirectory(prefix="test_rclone_backend_") as temp_dir:
            backend = RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user",
                password="password",
            )
            backend.set_base_remote_dir_path("/remote")
            backend.set_base_local_dir_path(temp_dir)
            backend.use_temp_file = True

            backend.queue("movie.mkv", False)

            self.assertEqual(1, len(_FakePopen.instances))
            command = _FakePopen.instances[0].command
            self.assertEqual("copyto", command[1])
            self.assertTrue(command[-1].endswith("movie.mkv.lftp"))

            with open(os.path.join(temp_dir, "movie.mkv.lftp"), "wb") as handle:
                handle.write(b"x" * 128)

            statuses = backend.status()
            self.assertEqual(1, len(statuses))
            self.assertEqual("movie.mkv", statuses[0].name)
            self.assertEqual(128, statuses[0].total_transfer_state.size_local)

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    @patch("transfer.rclone_backend.subprocess.Popen", side_effect=_FakePopen)
    def test_status_reaps_successful_file_and_renames_temp_file(self, _mock_popen, _mock_run, _mock_which):
        with tempfile.TemporaryDirectory(prefix="test_rclone_backend_") as temp_dir:
            backend = RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user",
                password="password",
            )
            backend.set_base_remote_dir_path("/remote")
            backend.set_base_local_dir_path(temp_dir)
            backend.use_temp_file = True

            backend.queue("movie.mkv", False)

            temp_path = os.path.join(temp_dir, "movie.mkv.lftp")
            final_path = os.path.join(temp_dir, "movie.mkv")
            with open(temp_path, "wb") as handle:
                handle.write(b"x" * 64)

            _FakePopen.instances[0]._returncode = 0
            statuses = backend.status()

            self.assertEqual([], statuses)
            self.assertFalse(os.path.exists(temp_path))
            self.assertTrue(os.path.exists(final_path))

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    @patch("transfer.rclone_backend.subprocess.Popen", side_effect=_FakePopen)
    def test_kill_terminates_matching_running_job(self, _mock_popen, _mock_run, _mock_which):
        with tempfile.TemporaryDirectory(prefix="test_rclone_backend_") as temp_dir:
            backend = RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user",
                password="password",
            )
            backend.set_base_remote_dir_path("/remote")
            backend.set_base_local_dir_path(temp_dir)
            backend.queue("movie.mkv", False)

            self.assertTrue(backend.kill("movie.mkv"))
            self.assertTrue(_FakePopen.instances[0].terminated)

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    def test_exposes_controller_runtime_transfer_surface(self, _mock_run, _mock_which):
        backend = RcloneTransferBackend(
            address="remote.server.com",
            port=22,
            user="user",
            password="password",
        )

        for attribute_name in (
            "set_base_logger",
            "set_base_remote_dir_path",
            "set_base_local_dir_path",
            "set_path_pairs",
            "queue",
            "kill",
            "kill_all",
            "status",
            "raise_pending_error",
            "exit",
            "set_verbose_logging",
            "last_status_poll_healthy",
            "num_parallel_jobs",
            "num_parallel_files",
            "num_connections_per_root_file",
            "num_connections_per_dir_file",
            "num_max_total_connections",
            "use_temp_file",
            "rate_limit",
            "net_socket_buffer",
            "temp_file_name",
            "xfer_verify",
            "xfer_verify_command",
        ):
            self.assertTrue(hasattr(backend, attribute_name), attribute_name)

    @patch("transfer.rclone_backend.shutil.which", return_value=None)
    def test_init_rejects_missing_rclone_executable_with_controlled_error(self, _mock_which):
        with self.assertRaises(RcloneTransferError) as error:
            RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user",
                password="password",
            )

        self.assertIn("available on PATH", str(error.exception))

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    def test_key_auth_does_not_write_or_use_stale_password(self, mock_run, _mock_which):
        backend = RcloneTransferBackend(
            address="remote.server.com",
            port=22,
            user="user",
            password="stale-password",
            use_ssh_key=True,
        )

        self.assertEqual(0, mock_run.call_count)
        with open(backend._RcloneTransferBackend__config_path, "r", encoding="utf-8") as config_file:
            config_text = config_file.read()
        self.assertIn("key_use_agent = true", config_text)
        self.assertNotIn("pass =", config_text)

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    def test_init_rejects_control_characters_in_host_field(self, _mock_which):
        with self.assertRaises(RcloneTransferError) as error:
            RcloneTransferBackend(
                address="remote.server.com\npass = injected",
                port=22,
                user="user",
                password=None,
            )

        self.assertIn("control characters", str(error.exception))

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    def test_init_rejects_control_characters_in_user_field(self, _mock_which):
        with self.assertRaises(RcloneTransferError) as error:
            RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user\r\nshell = injected",
                password=None,
            )

        self.assertIn("control characters", str(error.exception))

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    @patch("transfer.rclone_backend.subprocess.Popen", side_effect=FileNotFoundError("missing rclone"))
    def test_queue_surfaces_missing_binary_as_controlled_error(self, _mock_popen, _mock_run, _mock_which):
        with tempfile.TemporaryDirectory(prefix="test_rclone_backend_") as temp_dir:
            backend = RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user",
                password="password",
            )
            backend.set_base_remote_dir_path("/remote")
            backend.set_base_local_dir_path(temp_dir)

            with self.assertRaises(RcloneTransferError) as error:
                backend.queue("movie.mkv", False)

        self.assertIn("available on PATH", str(error.exception))

    @patch("transfer.rclone_backend.shutil.which", return_value="rclone")
    @patch("transfer.rclone_backend.subprocess.run", return_value=_CompletedProcess())
    @patch("transfer.rclone_backend.subprocess.Popen", side_effect=_FakePopen)
    def test_pending_error_redacts_temp_rclone_config_path(self, _mock_popen, _mock_run, _mock_which):
        with tempfile.TemporaryDirectory(prefix="test_rclone_backend_") as temp_dir:
            backend = RcloneTransferBackend(
                address="remote.server.com",
                port=22,
                user="user",
                password="password",
            )
            backend.set_base_remote_dir_path("/remote")
            backend.set_base_local_dir_path(temp_dir)
            backend.queue("movie.mkv", False)

            process = _FakePopen.instances[0]
            process._returncode = 1
            process.communicate = lambda: ("", "config file {} failed".format(backend._RcloneTransferBackend__config_path))

            backend.status()
            with self.assertRaises(RcloneTransferError) as error:
                backend.raise_pending_error()

        self.assertNotIn(backend._RcloneTransferBackend__config_path, str(error.exception))
        self.assertIn("<rclone-config>", str(error.exception))
