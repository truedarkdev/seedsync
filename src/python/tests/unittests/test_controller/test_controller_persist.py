# Copyright 2017, Inderpreet Singh, All rights reserved.

import threading
import unittest
import json

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

    def test_from_str_migrates_legacy_colon_prefixed_keys(self):
        legacy_key = "12345678-1234-1234-1234-123456789abc:archive.zip"
        expected_key = "12345678-1234-1234-1234-123456789abc{}archive.zip".format(KEY_SEP)
        persist = ControllerPersist.from_str(json.dumps({
            "downloaded": [legacy_key, "plain-name"],
            "extracted": [legacy_key],
            "stopped": [legacy_key],
        }))
        self.assertIn(expected_key, persist.downloaded_file_names)
        self.assertIn(expected_key, persist.extracted_file_names)
        self.assertIn(expected_key, persist.stopped_file_names)
        self.assertIn("plain-name", persist.downloaded_file_names)
        self.assertNotIn(legacy_key, persist.downloaded_file_names)

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
