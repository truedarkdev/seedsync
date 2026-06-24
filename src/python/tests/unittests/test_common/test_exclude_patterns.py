# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest

from common.exclude_patterns import filter_excluded_files, parse_exclude_patterns
from system import SystemFile


class TestExcludePatterns(unittest.TestCase):
    def test_parse_trims_and_deduplicates_exact_patterns(self):
        self.assertEqual(
            ["*.nfo", "Sample/", "Sample"],
            parse_exclude_patterns(" *.nfo , *.nfo, Sample/ , Sample, , "),
        )

    def test_filter_keeps_files_when_pattern_string_is_blank(self):
        files = [SystemFile("keep.txt", 10, False)]

        self.assertEqual(files, filter_excluded_files(files, "   "))

    def test_trailing_slash_pattern_excludes_directory_tree_only(self):
        sample_dir = SystemFile("Sample", 200, True)
        sample_dir.add_child(SystemFile("keep.mkv", 150, False))
        sample_dir.add_child(SystemFile("skip.nfo", 50, False))
        files = [
            sample_dir,
            SystemFile("Sample.mkv", 25, False),
            SystemFile("keep.txt", 10, False),
        ]

        filtered = filter_excluded_files(files, "Sample/")

        self.assertEqual(["Sample.mkv", "keep.txt"], [file.name for file in filtered])

    def test_case_sensitive_matching_matches_lftp_contract(self):
        files = [
            SystemFile("INFO.NFO", 10, False),
            SystemFile("info.nfo", 10, False),
            SystemFile("movie.mkv", 100, False),
        ]

        filtered = filter_excluded_files(files, "*.nfo")

        self.assertEqual(["INFO.NFO", "movie.mkv"], [file.name for file in filtered])

    def test_relative_path_patterns_apply_case_sensitively(self):
        show = SystemFile("Show", 560, True)
        season = SystemFile("Season 1", 300, True)
        season.add_child(SystemFile("episode1.mkv", 100, False))
        season.add_child(SystemFile("episode1.nfo", 5, False))
        season_lower = SystemFile("season 1", 300, True)
        season_lower.add_child(SystemFile("episode2.mkv", 100, False))
        season_lower.add_child(SystemFile("episode2.nfo", 5, False))
        extras = SystemFile("Extras", 60, True)
        extras.add_child(SystemFile("bonus.mkv", 50, False))
        extras.add_child(SystemFile("skip.nfo", 10, False))
        show.add_child(season)
        show.add_child(season_lower)
        show.add_child(extras)

        filtered = filter_excluded_files([show], "Extras/*,Season */*.nfo")

        self.assertEqual(["Show"], [file.name for file in filtered])
        self.assertEqual(["Season 1", "season 1", "Extras"], [file.name for file in filtered[0].children])
        self.assertEqual(["episode1.mkv"], [file.name for file in filtered[0].children[0].children])
        self.assertEqual(100, filtered[0].children[0].size)
        self.assertEqual(["episode2.mkv", "episode2.nfo"], [file.name for file in filtered[0].children[1].children])
        self.assertEqual(105, filtered[0].children[1].size)
        self.assertEqual([], [file.name for file in filtered[0].children[2].children])
        self.assertEqual(0, filtered[0].children[2].size)
        self.assertEqual(205, filtered[0].size)

    def test_recursive_filter_prunes_nested_children_and_recomputes_sizes(self):
        season = SystemFile("Season 1", 300, True)
        season.add_child(SystemFile("episode1.mkv", 100, False))
        season.add_child(SystemFile("episode1.nfo", 5, False))
        season.add_child(SystemFile("notes.txt", 3, False))
        series = SystemFile("Series", 350, True)
        series.add_child(season)
        series.add_child(SystemFile("poster.jpg", 50, False))

        filtered = filter_excluded_files([series], "*.nfo,*.txt")

        self.assertEqual(1, len(filtered))
        self.assertEqual("Series", filtered[0].name)
        self.assertEqual(150, filtered[0].size)
        self.assertEqual(["Season 1", "poster.jpg"], [file.name for file in filtered[0].children])
        self.assertEqual(100, filtered[0].children[0].size)
        self.assertEqual(["episode1.mkv"], [file.name for file in filtered[0].children[0].children])
        self.assertEqual(100, filtered[0].children[0].children[0].size)
