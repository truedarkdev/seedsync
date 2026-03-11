# Copyright 2026, SeedSync Contributors, All rights reserved.

import os
import shutil
import tempfile
import unittest

from common import PathPair, PathPairError, PathPairManager


class TestPathPairManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_path_pair")
        self.manager = PathPairManager(self.temp_dir)
        self.manager.load()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_add_update_remove_and_reload_pair(self):
        pair = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        self.manager.add_pair(pair)

        self.assertEqual(1, len(self.manager.get_all_pairs()))
        self.assertTrue(os.path.isfile(self.manager.file_path))

        updated = PathPair(
            id=pair.id,
            name="Films",
            remote_path="/remote/films",
            local_path="/local/films",
            enabled=False,
            auto_queue=False
        )
        self.manager.update_pair(updated)

        reloaded = PathPairManager(self.temp_dir)
        reloaded.load()
        loaded = reloaded.get_pair_by_id(pair.id)
        self.assertEqual("Films", loaded.name)
        self.assertEqual("/remote/films", loaded.remote_path)
        self.assertEqual(False, loaded.enabled)
        self.assertEqual(False, loaded.auto_queue)

        reloaded.remove_pair(pair.id)
        self.assertEqual([], reloaded.get_all_pairs())

    def test_reorder_pairs_requires_complete_id_set(self):
        first = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        second = PathPair(name="TV", remote_path="/remote/tv", local_path="/local/tv")
        self.manager.add_pair(first)
        self.manager.add_pair(second)

        self.manager.reorder_pairs([second.id, first.id])
        self.assertEqual([second.id, first.id], [pair.id for pair in self.manager.get_all_pairs()])

        with self.assertRaises(PathPairError):
            self.manager.reorder_pairs([first.id])

    def test_validate_rejects_non_string_paths(self):
        with self.assertRaises(PathPairError):
            PathPair(name="Invalid", remote_path=123, local_path="/local").validate()

        with self.assertRaises(PathPairError):
            PathPair(name="Invalid", remote_path="/remote", local_path=[]).validate()
