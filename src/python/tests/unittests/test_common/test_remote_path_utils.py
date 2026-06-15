# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest

from common import escape_remote_path_for_shell


class TestEscapeRemotePathForShell(unittest.TestCase):
    def test_quotes_literal_paths(self):
        self.assertEqual("/some/path", escape_remote_path_for_shell("/some/path"))
        self.assertEqual("'path with spaces'", escape_remote_path_for_shell("path with spaces"))
        self.assertEqual("'value;rm -rf /'", escape_remote_path_for_shell("value;rm -rf /"))
        self.assertEqual("'value$HOME`whoami*?['", escape_remote_path_for_shell("value$HOME`whoami*?["))

    def test_handles_empty_string(self):
        self.assertEqual("''", escape_remote_path_for_shell(""))

    def test_allows_safe_tilde_expansion(self):
        self.assertEqual("~", escape_remote_path_for_shell("~", allow_tilde_expansion=True))
        self.assertEqual("~/", escape_remote_path_for_shell("~/", allow_tilde_expansion=True))
        self.assertEqual("~/data/torrents", escape_remote_path_for_shell("~/data/torrents", allow_tilde_expansion=True))
        self.assertEqual("~'/data with spaces'", escape_remote_path_for_shell("~/data with spaces", allow_tilde_expansion=True))
        self.assertEqual("~user/downloads", escape_remote_path_for_shell("~user/downloads", allow_tilde_expansion=True))

    def test_does_not_expand_unsafe_tilde_prefixes(self):
        self.assertEqual("'~;rm -rf /'", escape_remote_path_for_shell("~;rm -rf /", allow_tilde_expansion=True))
        self.assertEqual("'~ user/downloads'", escape_remote_path_for_shell("~ user/downloads", allow_tilde_expansion=True))
