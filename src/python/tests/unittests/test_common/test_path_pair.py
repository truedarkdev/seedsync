# Copyright 2026, SeedSync Contributors, All rights reserved.

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from common import PathPair, PathPairConflictError, PathPairError, PathPairManager


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

    def test_add_pair_rejects_duplicate_name(self):
        first = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        second = PathPair(name="Movies", remote_path="/remote/tv", local_path="/local/tv")
        self.manager.add_pair(first)

        with self.assertRaises(PathPairConflictError) as context:
            self.manager.add_pair(second)

        self.assertEqual("Path pair with name 'Movies' already exists", str(context.exception))

    def test_update_pair_rejects_duplicate_name_for_other_pair(self):
        first = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        second = PathPair(name="TV", remote_path="/remote/tv", local_path="/local/tv")
        self.manager.add_pair(first)
        self.manager.add_pair(second)

        with self.assertRaises(PathPairConflictError) as context:
            self.manager.update_pair(PathPair(
                id=second.id,
                name="Movies",
                remote_path="/remote/tv",
                local_path="/local/tv"
            ))

        self.assertEqual("Path pair with name 'Movies' already exists", str(context.exception))

    def test_update_pair_allows_keeping_own_name(self):
        pair = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        self.manager.add_pair(pair)

        warnings = self.manager.update_pair(PathPair(
            id=pair.id,
            name="Movies",
            remote_path="/remote/films",
            local_path="/local/films"
        ))

        self.assertEqual([], warnings)
        updated = self.manager.get_pair_by_id(pair.id)
        self.assertEqual("/remote/films", updated.remote_path)
        self.assertEqual("/local/films", updated.local_path)

    def test_reorder_pairs_requires_complete_id_set(self):
        first = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        second = PathPair(name="TV", remote_path="/remote/tv", local_path="/local/tv")
        self.manager.add_pair(first)
        self.manager.add_pair(second)

        self.manager.reorder_pairs([second.id, first.id])
        self.assertEqual([second.id, first.id], [pair.id for pair in self.manager.get_all_pairs()])

        with self.assertRaises(PathPairError):
            self.manager.reorder_pairs([first.id])

    def test_load_backs_up_and_recovers_from_malformed_file(self):
        with open(self.manager.file_path, "w", encoding="utf-8") as handle:
            handle.write("{\"path_pairs\": [null]}")

        recovered = PathPairManager(self.temp_dir)
        recovered.load()

        self.assertEqual([], recovered.get_all_pairs())
        self.assertTrue(os.path.isfile(self.manager.file_path + ".1.bak"))

    def test_validate_rejects_non_string_paths(self):
        with self.assertRaises(PathPairError):
            PathPair(name="Invalid", remote_path=123, local_path="/local").validate()

        with self.assertRaises(PathPairError):
            PathPair(name="Invalid", remote_path="/remote", local_path=[]).validate()

    @patch("common.path_pair.is_running_in_docker", return_value=True)
    def test_validate_returns_docker_warning_for_non_downloads_path(self, _):
        warnings = PathPair(
            name="Movies",
            remote_path="/remote/movies",
            local_path="/media/movies"
        ).validate()

        self.assertEqual(1, len(warnings))
        self.assertIn("/media/movies", warnings[0])
        self.assertIn("/downloads", warnings[0])
        self.assertIn("/mounts", warnings[0])

    @patch("common.path_pair.is_running_in_docker", return_value=True)
    def test_validate_allows_mounts_path_in_docker(self, _):
        warnings = PathPair(
            name="Movies",
            remote_path="/remote/movies",
            local_path="/mounts/nas/movies"
        ).validate()

        self.assertEqual([], warnings)
