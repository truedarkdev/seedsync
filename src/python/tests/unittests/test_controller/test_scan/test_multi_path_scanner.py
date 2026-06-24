# Copyright 2026, SeedSync Contributors, All rights reserved.

import unittest
from unittest.mock import MagicMock

from controller.scan import MultiPathLocalScanner, MultiPathRemoteScanner, ScannerError
from system import SystemFile


class TestMultiPathRemoteScanner(unittest.TestCase):
    def test_reports_partial_files_and_aggregate_recoverable_error(self):
        partial_success_file = SystemFile("movie.mkv", 1234, False)
        partial_failure_file = SystemFile("episode.mkv", 2222, False)

        successful_scanner = MagicMock()
        successful_scanner.path_pair_id = "movies"
        successful_scanner.path_pair_name = "Movies"
        successful_scanner.scan.return_value = [partial_success_file]

        failing_scanner = MagicMock()
        failing_scanner.path_pair_id = "tv"
        failing_scanner.path_pair_name = "TV"
        failing_scanner.scan.side_effect = ScannerError(
            "temporary remote failure",
            recoverable=True,
            files=[partial_failure_file]
        )

        scanner = MultiPathRemoteScanner([successful_scanner, failing_scanner])

        with self.assertRaises(ScannerError) as ctx:
            scanner.scan()

        self.assertTrue(ctx.exception.recoverable)
        self.assertEqual([partial_success_file, partial_failure_file], ctx.exception.files)
        self.assertEqual("movies", ctx.exception.files[0].path_pair_id)
        self.assertEqual("Movies", ctx.exception.files[0].path_pair_name)
        self.assertEqual("tv", ctx.exception.files[1].path_pair_id)
        self.assertEqual("TV", ctx.exception.files[1].path_pair_name)
        self.assertIn("TV", str(ctx.exception))
        self.assertIn("temporary remote failure", str(ctx.exception))


class TestMultiPathLocalScanner(unittest.TestCase):
    def test_aggregates_recovered_managed_extract_file_ids(self):
        scanner_one = MagicMock()
        scanner_one.pop_managed_extract_file_ids.return_value = ["movie.zip", "series.zip"]
        scanner_two = MagicMock()
        scanner_two.pop_managed_extract_file_ids.return_value = ["series.zip", "episode.zip"]

        scanner = MultiPathLocalScanner([scanner_one, scanner_two])

        self.assertEqual(
            ["episode.zip", "movie.zip", "series.zip"],
            scanner.pop_managed_extract_file_ids()
        )

    def test_scans_only_targeted_path_pair_when_targeted(self):
        movie_file = SystemFile("movie.mkv", 1234, False)
        movie_scanner = MagicMock()
        movie_scanner.path_pair_id = "movies"
        movie_scanner.path_pair_name = "Movies"
        movie_scanner.scan.return_value = [movie_file]

        episode_file = SystemFile("episode.mkv", 4321, False)
        episode_scanner = MagicMock()
        episode_scanner.path_pair_id = "tv"
        episode_scanner.path_pair_name = "TV"
        episode_scanner.scan.return_value = [episode_file]

        scanner = MultiPathLocalScanner([movie_scanner, episode_scanner])
        scanner.set_scan_target_path_pair_ids({"tv"})

        results = scanner.scan()

        self.assertEqual([episode_file], results)
        movie_scanner.scan.assert_not_called()
        episode_scanner.scan.assert_called_once_with()
        self.assertEqual("tv", results[0].path_pair_id)
        self.assertEqual("TV", results[0].path_pair_name)


if __name__ == "__main__":
    unittest.main()
