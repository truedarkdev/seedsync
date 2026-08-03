# Copyright 2017, Inderpreet Singh, All rights reserved.

import threading
import unittest
import json
import tempfile
from pathlib import Path

from common import PersistError
from controller import ControllerPersist
from controller.persist_keys import KEY_SEP, persist_key, strip_persist_key


class TestControllerPersist(unittest.TestCase):
    def test_move_failure_counts_round_trip_and_missing_key_is_backward_compatible(self):
        file_id = '["movies","movie.mkv"]'
        persist = ControllerPersist()
        persist.move_failure_counts[file_id] = 4

        restored = ControllerPersist.from_str(persist.to_str())
        legacy = ControllerPersist.from_str('{"downloaded": [], "extracted": []}')

        self.assertEqual({file_id: 4}, restored.move_failure_counts)
        self.assertEqual({}, legacy.move_failure_counts)

    def test_final_move_succeeded_round_trip_and_missing_key_is_backward_compatible(self):
        file_id = '["movies","movie.mkv"]'
        persist = ControllerPersist()
        persist.final_move_succeeded_file_names.add(file_id)

        restored = ControllerPersist.from_str(persist.to_str())
        legacy = ControllerPersist.from_str('{"downloaded": [], "extracted": []}')

        self.assertEqual({file_id}, restored.final_move_succeeded_file_names)
        self.assertEqual(set(), legacy.final_move_succeeded_file_names)

    def test_move_failure_counts_drops_invalid_entries(self):
        restored = ControllerPersist.from_str(
            '{"downloaded": [], "extracted": [], "move_failure_counts": '
            '{"valid": 3, "negative": -1, "too_large": 5, "boolean": true, "text": "4"}}'
        )

        self.assertEqual({"valid": 3}, restored.move_failure_counts)
    def test_from_str(self):
        content = """
        {
            "downloaded": ["one", "two", "th ree", "fo.ur"],
            "extracted": ["fi\\"ve", "si@x", "se\\\\ven", "ei-ght"]
        }
        """
        persist = ControllerPersist.from_str(content)
        golden_downloaded = {"one", "two", "th ree", "fo.ur"}
        golden_extracted = {"fi\"ve", "si@x", "se\\ven", "ei-ght"}
        self.assertEqual(golden_downloaded, persist.downloaded_file_names)
        self.assertEqual(golden_extracted, persist.extracted_file_names)
        self.assertEqual(set(), persist.stopped_file_names)

    def test_to_str(self):
        persist = ControllerPersist()
        persist.downloaded_file_names.add("one")
        persist.downloaded_file_names.add("two")
        persist.downloaded_file_names.add("th ree")
        persist.downloaded_file_names.add("fo.ur")
        persist.extracted_file_names.add("fi\"ve")
        persist.extracted_file_names.add("si@x")
        persist.extracted_file_names.add("se\\ven")
        persist.extracted_file_names.add("ei-ght")
        persist.stopped_file_names.add("stopped-one")
        dct = json.loads(persist.to_str())
        self.assertTrue("downloaded" in dct)
        self.assertEqual({"one", "two", "th ree", "fo.ur"}, set(dct["downloaded"]))
        self.assertTrue("extracted" in dct)
        self.assertEqual({"fi\"ve", "si@x", "se\\ven", "ei-ght"}, set(dct["extracted"]))
        self.assertTrue("stopped" in dct)
        self.assertEqual({"stopped-one"}, set(dct["stopped"]))

    def test_to_and_from_str(self):
        persist = ControllerPersist()
        persist.downloaded_file_names.add("one")
        persist.downloaded_file_names.add("two")
        persist.downloaded_file_names.add("th ree")
        persist.downloaded_file_names.add("fo.ur")
        persist.extracted_file_names.add("fi\"ve")
        persist.extracted_file_names.add("si@x")
        persist.extracted_file_names.add("se\\ven")
        persist.extracted_file_names.add("ei-ght")
        persist.stopped_file_names.add("stopped-one")

        persist_actual = ControllerPersist.from_str(persist.to_str())
        self.assertEqual(
            persist.downloaded_file_names,
            persist_actual.downloaded_file_names
        )
        self.assertEqual(
            persist.extracted_file_names,
            persist_actual.extracted_file_names
        )
        self.assertEqual(
            persist.stopped_file_names,
            persist_actual.stopped_file_names
        )

    def test_to_str_is_stable_while_sets_change(self):
        persist = ControllerPersist()
        persist.downloaded_file_names.update({"one", "two"})
        persist.extracted_file_names.update({"three", "four"})
        persist.stopped_file_names.update({"five"})

        stop = threading.Event()

        def mutate_sets():
            names = ("alpha", "beta", "gamma")
            i = 0
            while not stop.is_set():
                name = names[i % len(names)]
                with persist.state_transaction():
                    persist.downloaded_file_names.add(name)
                    persist.extracted_file_names.add(name)
                    persist.stopped_file_names.add(name)
                    persist.downloaded_file_names.discard(name)
                    persist.extracted_file_names.discard(name)
                    persist.stopped_file_names.discard(name)
                i += 1

        thread = threading.Thread(target=mutate_sets)
        thread.start()
        try:
            for _ in range(250):
                round_tripped = ControllerPersist.from_str(persist.to_str())
                self.assertIsInstance(round_tripped, ControllerPersist)
        finally:
            stop.set()
            thread.join()

    def test_to_str_blocks_mutation_during_collection_snapshot(self):
        iterating = threading.Event()
        continue_iteration = threading.Event()
        mutation_started = threading.Event()
        mutation_finished = threading.Event()

        class PausingSet(set):
            def __iter__(self):
                iterator = super().__iter__()
                yield next(iterator)
                iterating.set()
                if not continue_iteration.wait(2):
                    raise AssertionError("mutation did not run")
                yield from iterator

        persist = ControllerPersist()
        persist.downloaded_file_names = PausingSet({"one", "two"})
        serialization_result = {}

        def serialize():
            serialization_result["content"] = persist.to_str()

        def mutate():
            mutation_started.set()
            with persist.state_transaction():
                persist.downloaded_file_names.add("three")
            mutation_finished.set()

        serialization_thread = threading.Thread(target=serialize)
        serialization_thread.start()
        self.assertTrue(iterating.wait(2))

        mutation_thread = threading.Thread(target=mutate)
        mutation_thread.start()
        self.assertTrue(mutation_started.wait(2))
        self.assertFalse(mutation_finished.wait(0.05))
        continue_iteration.set()

        serialization_thread.join(2)
        mutation_thread.join(2)
        self.assertFalse(serialization_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertEqual(
            {"one", "two"},
            set(json.loads(serialization_result["content"])["downloaded"]),
        )
        self.assertIn("three", persist.downloaded_file_names)

    def test_to_str_observes_complete_multi_field_mutation(self):
        mutation_halfway = threading.Event()
        continue_mutation = threading.Event()
        serialization_started = threading.Event()
        serialization_finished = threading.Event()
        serialization_result = {}
        persist = ControllerPersist()

        def mutate():
            with persist.state_transaction():
                persist.downloaded_file_names.add("transaction")
                mutation_halfway.set()
                self.assertTrue(continue_mutation.wait(2))
                persist.extracted_file_names.add("transaction")

        def serialize():
            serialization_started.set()
            serialization_result["content"] = persist.to_str()
            serialization_finished.set()

        mutation_thread = threading.Thread(target=mutate)
        mutation_thread.start()
        self.assertTrue(mutation_halfway.wait(2))

        serialization_thread = threading.Thread(target=serialize)
        serialization_thread.start()
        self.assertTrue(serialization_started.wait(2))
        self.assertFalse(serialization_finished.wait(0.05))
        continue_mutation.set()

        mutation_thread.join(2)
        serialization_thread.join(2)
        self.assertFalse(mutation_thread.is_alive())
        self.assertFalse(serialization_thread.is_alive())
        dct = json.loads(serialization_result["content"])
        self.assertIn("transaction", dct["downloaded"])
        self.assertIn("transaction", dct["extracted"])

    def test_persist_key_helpers(self):
        self.assertEqual("plain-name", persist_key(None, "plain-name"))
        self.assertEqual("pair-id{}plain-name".format(KEY_SEP), persist_key("pair-id", "plain-name"))
        self.assertEqual(
            "plain-name",
            strip_persist_key("pair-id{}plain-name".format(KEY_SEP), "pair-id")
        )
        self.assertEqual(
            "plain-name",
            strip_persist_key("pair-id:plain-name", "pair-id")
        )

    def test_canonical_migration_is_two_phase_idempotent_and_covers_all_collections(self):
        legacy_key = "12345678-1234-1234-1234-123456789abc:archive.zip"
        default_pair_id = "default-pair"
        expected_scoped = '["12345678-1234-1234-1234-123456789abc","archive.zip"]'
        expected_default = '["default-pair","plain-name"]'
        persist = ControllerPersist.from_str(json.dumps({
            "downloaded": [legacy_key, "plain-name"],
            "extracted": [legacy_key, "plain-name"],
            "stopped": [legacy_key, "plain-name"],
            "move_failure_counts": {legacy_key: 2, expected_scoped: 4, "plain-name": 1},
            "final_move_succeeded": [legacy_key, "plain-name"],
        }))

        self.assertTrue(persist.canonicalize_file_identities(default_pair_id))
        phase_one = json.loads(persist.to_str())
        self.assertEqual(1, phase_one["marker_identity_migration"])
        self.assertIn(legacy_key, persist.downloaded_file_names)
        self.assertIn(expected_scoped, persist.downloaded_file_names)
        self.assertIn(expected_default, persist.downloaded_file_names)
        self.assertEqual(4, persist.move_failure_counts[expected_scoped])

        restarted = ControllerPersist.from_str(json.dumps(phase_one))
        self.assertTrue(restarted.canonicalize_file_identities(default_pair_id))
        phase_two = json.loads(restarted.to_str())
        self.assertEqual(2, phase_two["marker_identity_migration"])
        for collection in ("downloaded", "extracted", "stopped", "final_move_succeeded"):
            self.assertEqual({expected_scoped, expected_default}, set(phase_two[collection]))
        self.assertEqual({expected_scoped: 4, expected_default: 1}, phase_two["move_failure_counts"])
        self.assertFalse(restarted.canonicalize_file_identities(default_pair_id))

    def test_canonical_migration_drops_ambiguous_bare_history_and_preserves_unknown_scopes(self):
        unknown_pair_id = "unknown-pair"
        scoped_key = "{}{}movie.mkv".format(unknown_pair_id, KEY_SEP)
        persist = ControllerPersist.from_str(json.dumps({
            "downloaded": ["bare.mkv", scoped_key],
            "extracted": ["bare.mkv", scoped_key],
            "stopped": ["bare.mkv", scoped_key],
            "move_failure_counts": {"bare.mkv": 3, scoped_key: 2},
            "final_move_succeeded": ["bare.mkv", scoped_key],
        }))

        self.assertTrue(persist.canonicalize_file_identities())
        self.assertTrue(persist.canonicalize_file_identities())
        expected = '["unknown-pair","movie.mkv"]'
        self.assertEqual({expected}, persist.downloaded_file_names)
        self.assertEqual({expected}, persist.extracted_file_names)
        self.assertEqual({expected}, persist.stopped_file_names)
        self.assertEqual({expected: 2}, persist.move_failure_counts)
        self.assertEqual({expected}, persist.final_move_succeeded_file_names)

    def test_canonical_migration_reloads_three_same_basename_pairs_for_all_collections(self):
        movies_id = "movies"
        tv_id = "tv"
        anime_id = "12345678-1234-1234-1234-123456789abc"
        name = "same.mkv"
        canonical_movies = '["movies","same.mkv"]'
        canonical_tv = '["tv","same.mkv"]'
        canonical_anime = '["12345678-1234-1234-1234-123456789abc","same.mkv"]'
        tv_legacy = "{}{}{}".format(tv_id, KEY_SEP, name)
        anime_legacy = "{}:{}".format(anime_id, name)
        payload = {
            "downloaded": [canonical_movies, tv_legacy, anime_legacy],
            "extracted": [anime_legacy, canonical_movies, tv_legacy],
            "stopped": [tv_legacy, anime_legacy, canonical_movies],
            "move_failure_counts": {canonical_tv: 1, tv_legacy: 4, anime_legacy: 2, canonical_movies: 3},
            "final_move_succeeded": [canonical_movies, tv_legacy, anime_legacy],
        }
        expected_ids = {canonical_movies, canonical_tv, canonical_anime}
        persist = ControllerPersist.from_str(json.dumps(payload))

        self.assertTrue(persist.canonicalize_file_identities())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "controller.persist"
            persist.to_file(str(path))
            # Re-reading phase one simulates an interrupted startup after the
            # canonical additions, before legacy cleanup.
            reloaded = ControllerPersist.from_file(str(path))
            self.assertTrue(reloaded.canonicalize_file_identities())
            reloaded.to_file(str(path))
            final = ControllerPersist.from_file(str(path))

        self.assertEqual(expected_ids, final.downloaded_file_names)
        self.assertEqual(expected_ids, final.extracted_file_names)
        self.assertEqual(expected_ids, final.stopped_file_names)
        self.assertEqual(expected_ids, final.final_move_succeeded_file_names)
        self.assertEqual({canonical_movies: 3, canonical_tv: 4, canonical_anime: 2}, final.move_failure_counts)

    def test_from_str_preserves_legacy_inputs_until_startup_migration(self):
        legacy_key = "12345678-1234-1234-1234-123456789abc:archive.zip"
        persist = ControllerPersist.from_str(json.dumps({
            "downloaded": [legacy_key, "plain-name"],
            "extracted": [legacy_key],
            "stopped": [legacy_key],
        }))
        self.assertIn(legacy_key, persist.downloaded_file_names)
        self.assertIn("plain-name", persist.downloaded_file_names)

    def test_from_str_rejects_non_integer_marker_migration_phase(self):
        for phase in (True, 1.0, "1"):
            with self.subTest(phase=phase):
                with self.assertRaises(PersistError):
                    ControllerPersist.from_str(json.dumps({
                        "downloaded": [], "extracted": [], "marker_identity_migration": phase,
                    }))

    def test_persist_read_error(self):
        # bad pattern
        content = """
        {
            "downloaded": [bad string],
            "extracted": []
        }
        """
        with self.assertRaises(PersistError):
            ControllerPersist.from_str(content)
        content = """
        {
            "downloaded": [],
            "extracted": [bad string]
        }
        """
        with self.assertRaises(PersistError):
            ControllerPersist.from_str(content)

        # empty json
        content = ""
        with self.assertRaises(PersistError):
            ControllerPersist.from_str(content)

        # missing keys
        content = """
        {
            "downloaded": []
        }
        """
        with self.assertRaises(PersistError):
            ControllerPersist.from_str(content)
        content = """
        {
            "extracted": []
        }
        """
        with self.assertRaises(PersistError):
            ControllerPersist.from_str(content)

        # malformed
        content = "{"
        with self.assertRaises(PersistError):
            ControllerPersist.from_str(content)

    def test_from_str_rejects_malformed_shapes(self):
        with self.assertRaises(PersistError):
            ControllerPersist.from_str("""
            {
                "downloaded": null,
                "extracted": []
            }
            """)

        with self.assertRaises(PersistError):
            ControllerPersist.from_str("[]")

    def test_from_str_rejects_malformed_persisted_name_collections_without_partial_result(self):
        for field_name in ("downloaded", "extracted", "stopped"):
            for malformed_value in ({"not": "an array"}, ["valid-name", 42]):
                with self.subTest(field=field_name, value=malformed_value):
                    payload = {
                        "downloaded": ["downloaded-valid"],
                        "extracted": ["extracted-valid"],
                        "stopped": ["stopped-valid"],
                    }
                    payload[field_name] = malformed_value

                    with self.assertRaises(PersistError):
                        ControllerPersist.from_str(json.dumps(payload))
