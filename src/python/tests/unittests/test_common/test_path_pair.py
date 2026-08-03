# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from common import (
    PathPair, PathPairConflictError, PathPairError, PathPairManager, PersistError,
    legacy_default_path_pair_id,
)


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

    def test_failed_saves_roll_back_each_mutation(self):
        first = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        second = PathPair(name="TV", remote_path="/remote/tv", local_path="/local/tv")
        self.manager.add_pair(first)
        self.manager.add_pair(second)
        original_pairs = list(self.manager.get_all_pairs())

        mutations = (
            lambda: self.manager.add_pair(
                PathPair(name="Music", remote_path="/remote/music", local_path="/local/music")
            ),
            lambda: self.manager.update_pair(PathPair(
                id=first.id,
                name="Films",
                remote_path="/remote/films",
                local_path="/local/films",
            )),
            lambda: self.manager.remove_pair(first.id),
            lambda: self.manager.reorder_pairs([second.id, first.id]),
        )

        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with patch.object(self.manager, "save", side_effect=PersistError("write failed")):
                    with self.assertRaisesRegex(PersistError, "write failed"):
                        mutation()
                self.assertEqual(original_pairs, self.manager.get_all_pairs())

    def test_failed_atomic_replace_preserves_existing_file_and_memory(self):
        pair = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        self.manager.add_pair(pair)
        with open(self.manager.file_path, "r", encoding="utf-8") as handle:
            original_content = handle.read()

        with patch("common.path_pair.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(PersistError, "replace failed"):
                self.manager.remove_pair(pair.id)

        with open(self.manager.file_path, "r", encoding="utf-8") as handle:
            self.assertEqual(original_content, handle.read())
        self.assertEqual([pair], self.manager.get_all_pairs())
        self.assertEqual([], [name for name in os.listdir(self.temp_dir) if name.endswith(".tmp")])

    def test_reorder_persists_and_reloads_in_order(self):
        first = PathPair(name="Movies", remote_path="/remote/movies", local_path="/local/movies")
        second = PathPair(name="TV", remote_path="/remote/tv", local_path="/local/tv")
        self.manager.add_pair(first)
        self.manager.add_pair(second)

        self.manager.reorder_pairs([second.id, first.id])
        reloaded = PathPairManager(self.temp_dir)

        self.assertEqual([second.id, first.id], [pair.id for pair in reloaded.load().path_pairs])

    def test_migrate_from_config_imports_when_path_pairs_file_is_missing(self):
        self.assertFalse(os.path.exists(self.manager.file_path))

        self.assertTrue(self.manager.migrate_from_config("/remote/downloads", "/downloads"))

        self.assertEqual(1, len(self.manager.get_all_pairs()))
        self.assertEqual("Default", self.manager.get_all_pairs()[0].name)
        self.assertEqual(
            legacy_default_path_pair_id("/remote/downloads", "/downloads"),
            self.manager.get_all_pairs()[0].id,
        )
        self.assertTrue(os.path.isfile(self.manager.file_path))

    def test_legacy_default_id_is_deterministic_across_manager_restart(self):
        expected = legacy_default_path_pair_id("/remote/downloads", "/downloads")
        self.assertTrue(self.manager.migrate_from_config("/remote/downloads", "/downloads"))

        restarted = PathPairManager(self.temp_dir)
        self.assertEqual(expected, restarted.load().path_pairs[0].id)

    def test_migrate_from_config_preserves_persisted_empty_collection_after_restart(self):
        pair = PathPair(name="Downloads", remote_path="/remote/downloads", local_path="/downloads")
        self.manager.add_pair(pair)
        self.manager.remove_pair(pair.id)

        reloaded = PathPairManager(self.temp_dir)
        reloaded.load()
        self.assertEqual([], reloaded.get_all_pairs())
        self.assertTrue(os.path.isfile(reloaded.file_path))

        self.assertFalse(reloaded.migrate_from_config("/remote/legacy", "/downloads/legacy"))
        self.assertEqual([], reloaded.get_all_pairs())

    @patch("common.path_pair.os.path.lexists", return_value=True)
    def test_migrate_from_config_does_not_replace_existing_dangling_path_entry(self, _):
        self.assertFalse(self.manager.migrate_from_config("/remote/legacy", "/downloads/legacy"))
        self.assertEqual([], self.manager.get_all_pairs())
        self.assertFalse(os.path.exists(self.manager.file_path))

    def test_load_backs_up_and_recovers_from_malformed_file(self):
        with open(self.manager.file_path, "w", encoding="utf-8") as handle:
            handle.write("{\"path_pairs\": [null]}")

        recovered = PathPairManager(self.temp_dir)
        recovered.load()

        self.assertEqual([], recovered.get_all_pairs())
        self.assertTrue(os.path.isfile(self.manager.file_path + ".1.bak"))

    def test_from_str_rejects_non_object_top_level_json(self):
        for content in ("[]", "null", "42", '"value"'):
            with self.subTest(content=content):
                with self.assertRaises(PersistError):
                    self.manager.from_str(content)

    def test_load_backs_up_and_recovers_from_non_object_top_level_json(self):
        with open(self.manager.file_path, "w", encoding="utf-8") as handle:
            handle.write("[]")

        recovered = PathPairManager(self.temp_dir)
        recovered.load()

        self.assertEqual([], recovered.get_all_pairs())
        self.assertTrue(os.path.isfile(self.manager.file_path + ".1.bak"))

    def test_validate_rejects_non_string_paths(self):
        with self.assertRaises(PathPairError):
            PathPair(name="Invalid", remote_path=123, local_path="/local").validate()

        with self.assertRaises(PathPairError):
            PathPair(name="Invalid", remote_path="/remote", local_path=[]).validate()

    def test_validate_rejects_non_string_name_and_id(self):
        cases = [
            (dict(name=123, remote_path="/remote", local_path="/local"), "name must be a string"),
            (dict(id=123, name="Invalid", remote_path="/remote", local_path="/local"), "id must be a string"),
        ]

        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PathPairError) as context:
                    PathPair(**kwargs).validate()
                self.assertIn(expected, str(context.exception))

    def test_validate_rejects_non_boolean_flags(self):
        cases = [
            (dict(enabled="yes", auto_queue=True), "enabled must be a boolean"),
            (dict(enabled=True, auto_queue="yes"), "auto_queue must be a boolean"),
        ]

        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PathPairError) as context:
                    PathPair(
                        name="Invalid",
                        remote_path="/remote",
                        local_path="/local",
                        **kwargs
                    ).validate()
                self.assertIn(expected, str(context.exception))

    def test_load_backs_up_and_recovers_from_non_boolean_flags(self):
        with open(self.manager.file_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "path_pairs": [{
                    "id": "movies",
                    "name": "Movies",
                    "remote_path": "/remote/movies",
                    "local_path": "/local/movies",
                    "enabled": True,
                    "auto_queue": "yes",
                }]
            }))

        recovered = PathPairManager(self.temp_dir)
        recovered.load()

        self.assertEqual([], recovered.get_all_pairs())
        self.assertTrue(os.path.isfile(self.manager.file_path + ".1.bak"))

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
