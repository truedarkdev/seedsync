# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lftp import Lftp, LftpError
from common.exclude_patterns import ExactPathExclusion


class TestLftpQueueCommand(unittest.TestCase):
    def _make_lftp(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__base_remote_dir_path = "/remote/path"
        lftp._Lftp__base_local_dir_path = "/local/path"
        lftp._Lftp__run_command = MagicMock()
        return lftp

    @staticmethod
    def _local_destination_argument(local_dir):
        return Lftp._Lftp__quote_command_argument("{}/".format(local_dir))

    def test_queue_dir_uses_exclude_glob_for_mirror_commands(self):
        lftp = self._make_lftp()

        lftp.queue("show", True, exclude_patterns="*.nfo, Sample/")

        command = lftp._Lftp__run_command.call_args[0][0]
        self.assertEqual(
            'queue mirror -c --exclude-glob "*.nfo" --exclude-glob "Sample/" "/remote/path/show" "/local/path/"',
            command
        )

    def test_queue_dir_escapes_backslashes_and_quotes_in_exclude_patterns(self):
        lftp = self._make_lftp()

        lftp.queue("show", True, exclude_patterns=[r"Extras\Season", 'Quote "Test"'])

        command = lftp._Lftp__run_command.call_args[0][0]
        self.assertEqual(
            'queue mirror -c --exclude-glob "Extras\\\\Season" --exclude-glob "Quote \\"Test\\"" '
            '"/remote/path/show" "/local/path/"',
            command
        )

    def test_queue_dir_preserves_apostrophes_in_literal_arguments(self):
        lftp = self._make_lftp()
        lftp._Lftp__base_remote_dir_path = "/remote/pa'th"
        lftp._Lftp__base_local_dir_path = "/local/pa'th"

        lftp.queue("O'Reilly", True, exclude_patterns=["Season 1/O'Brian.nfo"])

        command = lftp._Lftp__run_command.call_args[0][0]
        self.assertEqual(
            'queue mirror -c --exclude-glob "Season 1/O\'Brian.nfo" '
            '"/remote/pa\'th/O\'Reilly" "/local/pa\'th/"',
            command
        )
        self.assertNotIn("queue '", command)

    def test_queue_dir_rejects_control_characters_in_exclude_patterns(self):
        lftp = self._make_lftp()

        with self.assertRaises(LftpError):
            lftp.queue("show", True, exclude_patterns=["bad\npattern"])

    def test_queue_file_ignores_exclude_patterns(self):
        lftp = self._make_lftp()

        lftp.queue("movie.mkv", False, exclude_patterns=["*.nfo", "Sample/"])

        command = lftp._Lftp__run_command.call_args[0][0]
        self.assertEqual(
            'queue pget -c "/remote/path/movie.mkv" -o "/local/path/"',
            command
        )

    def test_queue_file_uses_get_resume_for_sidecarless_existing_target(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"partial")

            lftp = self._make_lftp()
            lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
            command = lftp._Lftp__run_command.call_args[0][0]
            self.assertEqual(
                'queue get -c "/remote/path/movie.mkv" -o {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                command,
            )
            self.assertFalse(os.path.exists(target + ".lftp-pget-status"))

    def test_queue_file_preserves_multi_connection_resume_with_lftp_sidecar(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"partial")
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=7\n0.limit=100\n")

            lftp = self._make_lftp()
            lftp.queue(
                "movie.mkv",
                False,
                local_base_dir_path=local_dir,
            )

            command = lftp._Lftp__run_command.call_args[0][0]
            self.assertEqual(
                'queue pget -c "/remote/path/movie.mkv" -o {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                command
            )

    def test_queue_file_rejects_changed_source_with_valid_old_map_before_queueing(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv.lftp")
            with open(target, "wb") as handle:
                handle.write(b"old-partial")
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=7\n0.limit=100\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "Cannot safely restart"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir, allow_resume=False)
            lftp._Lftp__run_command.assert_not_called()

    def test_queue_file_rejects_changed_source_with_sidecarless_partial_before_queueing(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"old-partial")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "Cannot safely restart"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir, allow_resume=False)
            lftp._Lftp__run_command.assert_not_called()

    def test_queue_file_bootstraps_legacy_sidecarless_partial_with_contiguous_get(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"legacy")

            lftp = self._make_lftp()
            lftp.queue(
                "movie.mkv", False, local_base_dir_path=local_dir,
                allow_resume=False, allow_legacy_get_resume=True, expected_size=100,
            )

            self.assertEqual(
                'queue get -c "/remote/path/movie.mkv" -o {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                lftp._Lftp__run_command.call_args[0][0],
            )

    def test_queue_file_rejects_legacy_bootstrap_when_pget_map_exists(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"legacy")
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=7\n0.limit=100\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "pget status map"):
                lftp.queue(
                    "movie.mkv", False, local_base_dir_path=local_dir,
                    allow_resume=False, allow_legacy_get_resume=True, expected_size=100,
                )
            lftp._Lftp__run_command.assert_not_called()

    def test_queue_file_rejects_legacy_bootstrap_when_partial_exceeds_remote_size(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"x" * 101)

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "exceeds the remote size"):
                lftp.queue(
                    "movie.mkv", False, local_base_dir_path=local_dir,
                    allow_resume=False, allow_legacy_get_resume=True, expected_size=100,
                )
            lftp._Lftp__run_command.assert_not_called()

    def test_queue_file_allows_safe_fresh_start_when_no_partial_exists(self):
        with tempfile.TemporaryDirectory() as local_dir:
            lftp = self._make_lftp()
            lftp.queue("movie.mkv", False, local_base_dir_path=local_dir, allow_resume=False)

            command = lftp._Lftp__run_command.call_args[0][0]
            self.assertEqual(
                'queue pget -c "/remote/path/movie.mkv" -o {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                command,
            )

    def test_queue_file_legacy_timestamp_without_artifacts_starts_fresh_pget(self):
        with tempfile.TemporaryDirectory() as local_dir:
            lftp = self._make_lftp()

            lftp.queue(
                "movie.mkv", False, local_base_dir_path=local_dir,
                allow_resume=False, allow_legacy_get_resume=True, expected_size=100,
            )

            command = lftp._Lftp__run_command.call_args.args[0]
            self.assertEqual(
                'queue pget -c "/remote/path/movie.mkv" -o {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                command,
            )

    def test_delete_local_plan_accepts_regular_direct_temp_and_map_only_artifacts(self):
        with tempfile.TemporaryDirectory() as local_dir:
            direct = os.path.join(local_dir, "movie.mkv")
            direct_map = direct + ".lftp-pget-status"
            with open(direct_map, "w", encoding="utf-8") as handle:
                handle.write("malformed is removable only by Delete Local")
            self.assertEqual((direct_map,), Lftp.get_safe_file_artifact_delete_paths(local_dir, "movie.mkv"))

            with open(direct, "wb") as handle:
                handle.write(b"partial")
            self.assertEqual(
                (direct, direct_map), Lftp.get_safe_file_artifact_delete_paths(local_dir, "movie.mkv"),
            )
            os.unlink(direct)
            os.unlink(direct_map)

            temp = direct + ".lftp"
            with open(temp, "wb") as handle:
                handle.write(b"partial")
            self.assertEqual((temp,), Lftp.get_safe_file_artifact_delete_paths(local_dir, "movie.mkv"))

    def test_delete_local_plan_rejects_ambiguous_or_link_artifacts(self):
        with tempfile.TemporaryDirectory() as local_dir:
            direct = os.path.join(local_dir, "movie.mkv")
            with open(direct, "wb") as handle:
                handle.write(b"direct")
            with open(direct + ".lftp", "wb") as handle:
                handle.write(b"temp")
            with self.assertRaisesRegex(LftpError, "ambiguous"):
                Lftp.get_safe_file_artifact_delete_paths(local_dir, "movie.mkv")
            os.unlink(direct + ".lftp")
            os.unlink(direct)
            os.symlink(os.path.join(local_dir, "outside"), direct + ".lftp-pget-status")
            with self.assertRaisesRegex(LftpError, "unsafe"):
                Lftp.get_safe_file_artifact_delete_paths(local_dir, "movie.mkv")

    def test_queue_file_rejects_orphan_status_sidecar_without_normal_target(self):
        with tempfile.TemporaryDirectory() as local_dir:
            status_path = os.path.join(local_dir, "movie.mkv.lftp-pget-status")
            with open(status_path, "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=7\n0.limit=100\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "orphan"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)

    def test_queue_file_rejects_orphan_status_sidecar_without_lftp_temp_target(self):
        with tempfile.TemporaryDirectory() as local_dir:
            status_path = os.path.join(local_dir, "movie.mkv.lftp.lftp-pget-status")
            with open(status_path, "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=7\n0.limit=100\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "orphan"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)

    def test_queue_file_uses_get_resume_for_malformed_status_sidecar(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"partial")
            with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                handle.write("not an lftp segment map\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "status sidecar is invalid"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
            lftp._Lftp__run_command.assert_not_called()
            self.assertTrue(os.path.exists(target + ".lftp-pget-status"))

    def test_queue_file_rejects_empty_status_sidecar_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"partial")
            open(target + ".lftp-pget-status", "w", encoding="utf-8").close()

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "status sidecar is invalid"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
            lftp._Lftp__run_command.assert_not_called()
            self.assertTrue(os.path.exists(target + ".lftp-pget-status"))

    def test_queue_file_rejects_stale_status_map_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            status_path = target + ".lftp-pget-status"
            with open(target, "wb") as handle:
                handle.write(b"partial")
            with open(status_path, "w", encoding="utf-8") as handle:
                handle.write("size=99\n0.pos=7\n0.limit=99\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "status sidecar is stale"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir, expected_size=100)
            lftp._Lftp__run_command.assert_not_called()
            self.assertTrue(os.path.exists(status_path))

    def test_queue_file_uses_get_resume_for_unsafe_status_maps(self):
        status_maps = {
            "size-only": "size=100\n",
            "mismatched-index": "size=100\n0.pos=0\n1.limit=100\n",
            "duplicate-index": "size=100\n0.pos=0\n0.limit=50\n0.pos=50\n0.limit=100\n",
            "out-of-order-index": "size=100\n1.pos=0\n1.limit=100\n",
            "overlapping-ranges": "size=100\n0.pos=0\n0.limit=60\n1.pos=50\n1.limit=90\n",
        }
        for label, status_map in status_maps.items():
            with self.subTest(status_map=label), tempfile.TemporaryDirectory() as local_dir:
                target = os.path.join(local_dir, "movie.mkv")
                with open(target, "wb") as handle:
                    handle.write(b"partial")
                with open(target + ".lftp-pget-status", "w", encoding="utf-8") as handle:
                    handle.write(status_map)

                lftp = self._make_lftp()
                with self.assertRaisesRegex(LftpError, "status sidecar is invalid"):
                    lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
                lftp._Lftp__run_command.assert_not_called()
                self.assertTrue(os.path.exists(target + ".lftp-pget-status"))

    def test_queue_file_never_uses_get_for_malformed_map_beside_noncontiguous_partial(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            status_path = target + ".lftp-pget-status"
            with open(target, "wb") as handle:
                handle.write(b"partial-bytes-with-unknown-holes")
            with open(status_path, "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=not-a-number\n")

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "status sidecar is invalid"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
            lftp._Lftp__run_command.assert_not_called()
            self.assertTrue(os.path.exists(status_path))

    def test_queue_file_uses_get_resume_for_sidecarless_lftp_temp_target(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv.lftp")
            with open(target, "wb") as handle:
                handle.write(b"partial")

            lftp = self._make_lftp()
            lftp.queue(
                "movie.mkv",
                False,
                local_base_dir_path=local_dir,
            )

            command = lftp._Lftp__run_command.call_args[0][0]
            self.assertEqual(
                'queue get -c "/remote/path/movie.mkv" -o {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                command
            )

    def test_queue_file_rejects_path_identity_traversal_or_separator(self):
        with tempfile.TemporaryDirectory() as local_dir:
            lftp = self._make_lftp()
            for name in ("../outside.mkv", "nested/outside.mkv", r"nested\outside.mkv", ".", ".."):
                with self.subTest(name=name), self.assertRaises(LftpError):
                    lftp.queue(name, False, local_base_dir_path=local_dir)

    def test_queue_file_rejects_absolute_path_identity(self):
        with tempfile.TemporaryDirectory() as local_dir:
            absolute_name = os.path.join(local_dir, "outside.mkv")
            lftp = self._make_lftp()

            with self.assertRaises(LftpError):
                lftp.queue(absolute_name, False, local_base_dir_path=local_dir)

    def test_queue_file_rejects_symlink_target(self):
        with tempfile.TemporaryDirectory() as local_dir, tempfile.TemporaryDirectory() as outside_dir:
            target = os.path.join(local_dir, "movie.mkv")
            outside = os.path.join(outside_dir, "movie.mkv")
            with open(outside, "wb") as handle:
                handle.write(b"outside")
            try:
                os.symlink(outside, target)
            except (OSError, NotImplementedError) as error:
                self.skipTest("symlink creation unavailable: {}".format(error))

            lftp = self._make_lftp()
            with self.assertRaises(LftpError):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)

    def test_queue_file_does_not_follow_symlink_status_sidecar(self):
        with tempfile.TemporaryDirectory() as local_dir, tempfile.TemporaryDirectory() as outside_dir:
            target = os.path.join(local_dir, "movie.mkv")
            status_path = target + ".lftp-pget-status"
            outside_status = os.path.join(outside_dir, "status")
            with open(target, "wb") as handle:
                handle.write(b"partial")
            with open(outside_status, "w", encoding="utf-8") as handle:
                handle.write("size=100\n0.pos=7\n0.limit=100\n")
            try:
                os.symlink(outside_status, status_path)
            except (OSError, NotImplementedError) as error:
                self.skipTest("symlink creation unavailable: {}".format(error))

            lftp = self._make_lftp()
            with self.assertRaisesRegex(LftpError, "status sidecar is unsafe"):
                lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
            lftp._Lftp__run_command.assert_not_called()

    def test_queue_file_rejects_when_existing_target_cannot_be_inspected(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "movie.mkv")
            with open(target, "wb") as handle:
                handle.write(b"partial")

            real_stat = os.stat

            def stat_with_race(path, *args, **kwargs):
                if os.path.normpath(path) == os.path.normpath(target):
                    raise PermissionError("target changed while inspecting")
                return real_stat(path, *args, **kwargs)

            lftp = self._make_lftp()
            with patch("lftp.lftp.os.stat", side_effect=stat_with_race):
                with self.assertRaisesRegex(LftpError, "target is unsafe"):
                    lftp.queue("movie.mkv", False, local_base_dir_path=local_dir)
            lftp._Lftp__run_command.assert_not_called()

    def test_queue_dir_does_not_inspect_or_change_resume_connections(self):
        with tempfile.TemporaryDirectory() as local_dir:
            target = os.path.join(local_dir, "show")
            os.mkdir(target)

            lftp = self._make_lftp()
            lftp.queue(
                "show",
                True,
                local_base_dir_path=local_dir,
            )

            command = lftp._Lftp__run_command.call_args[0][0]
            self.assertEqual(
                'queue mirror -c "/remote/path/show" {}'.format(
                    self._local_destination_argument(local_dir)
                ),
                command
            )

    def test_queue_dir_renders_typed_exact_paths_as_anchored_regexes(self):
        lftp = self._make_lftp()

        lftp.queue(
            "show",
            True,
            exclude_patterns=[
                "*.nfo",
                ExactPathExclusion("E06.mkv"),
                ExactPathExclusion(r"nested/[E07]*?,comma\name.mkv"),
            ],
        )

        command = lftp._Lftp__run_command.call_args[0][0]
        self.assertIn('--exclude-glob "*.nfo"', command)
        self.assertIn('--exclude "^E06\\\\.mkv$"', command)
        self.assertIn('--exclude "^nested/\\\\[E07\\\\]\\\\*\\\\?,comma\\\\\\\\name\\\\.mkv$"', command)
