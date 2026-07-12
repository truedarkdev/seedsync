import json
import os
import tempfile
import unittest

from common.managed_extract import read_managed_extract_marker, resolve_managed_extract_file_id


class TestManagedExtractMarker(unittest.TestCase):
    def test_resolve_rejects_malformed_identity_fields(self):
        malformed_markers = (
            {},
            {"archive_name": 42},
            {"archive_name": "archive.zip", "path_pair_id": 42},
            {"archive_name": "archive.zip", "archive_file_id": 42},
        )
        for marker in malformed_markers:
            with self.subTest(marker=marker):
                self.assertIsNone(resolve_managed_extract_file_id(marker))

    def test_read_returns_none_for_non_object_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "marker.json")
            for value in ([], None, 42, "value"):
                with self.subTest(value=value):
                    with open(marker_path, "w", encoding="utf-8") as handle:
                        json.dump(value, handle)
                    self.assertIsNone(read_managed_extract_marker(marker_path))

    def test_read_accepts_legacy_marker_without_extracted_at(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "marker.json")
            with open(marker_path, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "archive_name": "archive.zip"}, handle)

            marker = read_managed_extract_marker(marker_path)

            self.assertIsNotNone(marker)
            self.assertEqual("archive.zip", resolve_managed_extract_file_id(marker))


if __name__ == "__main__":
    unittest.main()
