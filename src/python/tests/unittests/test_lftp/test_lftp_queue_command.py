# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock

from lftp import Lftp, LftpError


class TestLftpQueueCommand(unittest.TestCase):
    def _make_lftp(self):
        lftp = Lftp.__new__(Lftp)
        lftp.logger = MagicMock()
        lftp._Lftp__base_remote_dir_path = "/remote/path"
        lftp._Lftp__base_local_dir_path = "/local/path"
        lftp._Lftp__run_command = MagicMock()
        return lftp

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
