# Copyright 2026, SeedSync Contributors, All rights reserved.

import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from system import SystemFile
from scan_fs import SystemScanner, stream_root_fingerprint


class TestScanFsScript(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_scan_fs_")
        self.script_path = Path(__file__).resolve().parents[2] / "scan_fs.py"

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _write_file(self, *relative_parts: str, content: bytes):
        path = os.path.join(self.temp_dir, *relative_parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)

    def _run_scan_fs(self, *scan_args: str, check: bool):
        wrapper = (
            "import runpy, sys; "
            "sys.hexversion = 0x03080000; "
            "sys.argv = {argv!r}; "
            "runpy.run_path({script!r}, run_name='__main__')"
        ).format(
            argv=[str(self.script_path), *scan_args],
            script=str(self.script_path),
        )
        return subprocess.run(
            [sys.executable, "-c", wrapper],
            capture_output=True,
            check=check,
            text=True,
        )

    def test_emits_json_that_system_file_can_parse(self):
        self._write_file("alpha.txt", content=b"abc")
        self._write_file("nested", "beta.bin", content=b"wxyz")

        result = self._run_scan_fs(self.temp_dir, check=True)

        payload = json.loads(result.stdout)
        self.assertIsInstance(payload, list)

        files = [SystemFile.from_dict(file_dict) for file_dict in payload]
        self.assertEqual(["alpha.txt", "nested"], [file.name for file in files])

        alpha = files[0]
        nested = files[1]
        self.assertFalse(alpha.is_dir)
        self.assertEqual(3, alpha.size)
        self.assertEqual(os.stat(os.path.join(self.temp_dir, "alpha.txt")).st_mtime_ns, alpha.mtime_ns)
        self.assertTrue(nested.is_dir)
        self.assertEqual(4, nested.size)
        self.assertEqual(1, len(nested.children))
        self.assertEqual("beta.bin", nested.children[0].name)
        self.assertEqual(4, nested.children[0].size)

    def test_reports_missing_path_as_system_scanner_error(self):
        missing_path = os.path.join(self.temp_dir, "missing")

        result = self._run_scan_fs(missing_path, check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("SystemScannerError: Path does not exist", result.stderr)

    def test_stream_reports_partial_scan_without_complete_on_permission_error(self):
        self._write_file("nested", "secret.bin", content=b"secret")
        blocked_path = os.path.abspath(os.path.join(self.temp_dir, "nested"))
        real_scandir = os.scandir

        def guarded_scandir(path):
            if os.path.abspath(os.fspath(path)) == blocked_path:
                raise PermissionError(13, "Permission denied", path)
            return real_scandir(path)

        stdout = io.StringIO()
        argv = [str(self.script_path), "--stream", "--stream-batch-size", "1", self.temp_dir]
        with patch.object(os, "scandir", side_effect=guarded_scandir):
            with patch.object(sys, "argv", argv):
                with redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as context:
                        runpy.run_path(str(self.script_path), run_name="__main__")

        self.assertNotEqual(0, context.exception.code)
        self.assertIn('"type":"manifest_begin"', stdout.getvalue())
        self.assertIn("SystemScannerError: Permission denied while scanning", str(context.exception.code))
        self.assertNotIn('"type": "complete"', stdout.getvalue())

    def test_stream_flattens_a_large_recursive_root_into_bounded_node_batches(self):
        for index in range(800):
            self._write_file("huge", "child-{:04d}-{}.bin".format(index, "x" * 80), content=b"")

        result = self._run_scan_fs("--stream", self.temp_dir, check=True)

        records = [line.encode("utf-8") for line in result.stdout.splitlines(keepends=True)]
        self.assertTrue(records)
        self.assertTrue(all(len(record) <= 64 * 1024 for record in records))
        payloads = [json.loads(record.decode("utf-8").split("\t", 1)[1]) for record in records]
        self.assertEqual("manifest_begin", payloads[0]["type"])
        self.assertIn("manifest_end", [payload["type"] for payload in payloads])
        self.assertNotIn("roots", [payload["type"] for payload in payloads])
        root_start = next(payload for payload in payloads if payload["type"] == "root_begin")
        self.assertEqual("huge", root_start["name"])
        batches = [payload for payload in payloads if payload["type"] == "root_nodes"]
        self.assertTrue(batches)
        self.assertTrue(all(1 <= len(batch["nodes"]) <= 64 for batch in batches))
        nodes = [node for batch in batches for node in batch["nodes"]]
        self.assertEqual(801, len(nodes))
        self.assertEqual("huge", nodes[0]["file"]["name"])
        self.assertNotIn("children", nodes[0]["file"])
        self.assertEqual(800, sum(node["parent"] == 0 for node in nodes[1:]))

    def test_stream_uses_full_tree_fingerprint_only_when_nested_root_is_unchanged(self):
        self._write_file("root", "nested.bin", content=b"one")
        scanner = SystemScanner(self.temp_dir)
        root = scanner.scan_single("root")
        fingerprint = stream_root_fingerprint(root)
        decoded_root = SystemFile.from_dict(root.to_dict())
        decoded_root.path_pair_id = "pair"
        decoded_root.path_pair_name = "Pair"
        self.assertEqual(fingerprint, stream_root_fingerprint(decoded_root))

        unchanged = self._run_scan_fs(
            "--stream", "--stream-known-root-fingerprints", json.dumps({"root": fingerprint}), self.temp_dir,
            check=True,
        )
        unchanged_records = [json.loads(line.split("\t", 1)[1]) for line in unchanged.stdout.splitlines()]
        self.assertEqual(
            [{"type": "root_unchanged", "name": "root", "fingerprint": fingerprint}],
            [record for record in unchanged_records if record["type"] == "root_unchanged"],
        )
        self.assertNotIn("root_begin", [record["type"] for record in unchanged_records])

        self._write_file("root", "nested.bin", content=b"changed")
        changed = self._run_scan_fs(
            "--stream", "--stream-known-root-fingerprints", json.dumps({"root": fingerprint}), self.temp_dir,
            check=True,
        )
        changed_records = [json.loads(line.split("\t", 1)[1]) for line in changed.stdout.splitlines()]
        self.assertIn("root_begin", [record["type"] for record in changed_records])
        self.assertNotIn("root_unchanged", [record["type"] for record in changed_records])

    def test_stream_root_fingerprint_uses_iterative_bounded_depth_walk(self):
        root = SystemFile("root", 0, is_dir=True)
        parent = root
        for index in range(1500):
            child = SystemFile("level-{}".format(index), 0, is_dir=True)
            parent.add_child(child)
            parent = child

        fingerprint = stream_root_fingerprint(root)

        self.assertEqual(64, len(fingerprint))
        self.assertTrue(all(character in "0123456789abcdef" for character in fingerprint))

    def test_stream_root_fingerprint_respects_order_and_exclusion_filtered_tree(self):
        root = SystemFile("root", 3, is_dir=True)
        root.add_child(SystemFile("a.bin", 1, mtime_ns=1))
        root.add_child(SystemFile("b.bin", 2, mtime_ns=2))
        reversed_root = SystemFile("root", 3, is_dir=True)
        reversed_root.add_child(SystemFile("b.bin", 2, mtime_ns=2))
        reversed_root.add_child(SystemFile("a.bin", 1, mtime_ns=1))
        changed_metadata = SystemFile("root", 3, is_dir=True)
        changed_metadata.add_child(SystemFile("a.bin", 1, mtime_ns=9))
        changed_metadata.add_child(SystemFile("b.bin", 2, mtime_ns=2))

        self.assertNotEqual(stream_root_fingerprint(root), stream_root_fingerprint(reversed_root))
        self.assertNotEqual(stream_root_fingerprint(root), stream_root_fingerprint(changed_metadata))

        self._write_file("root", "visible.bin", content=b"same")
        self._write_file("root", ".ignored.bin", content=b"first")
        scanner = SystemScanner(self.temp_dir)
        scanner.add_exclude_prefix(".")
        before = stream_root_fingerprint(scanner.scan_single("root"))
        self._write_file("root", ".ignored.bin", content=b"changed")
        after = stream_root_fingerprint(scanner.scan_single("root"))
        self.assertEqual(before, after)

    def test_stream_root_fingerprint_covers_every_v2_visible_node_field(self):
        created = datetime(2026, 1, 1)
        modified = created + timedelta(seconds=1)

        def leaf(**overrides):
            values = {
                "name": "file.bin", "size": 1, "is_dir": False,
                "time_created": created, "time_modified": modified,
                "mtime_ns": 1, "is_staging": False,
            }
            values.update(overrides)
            collision = bool(values.pop("has_staging_collision", False))
            file = SystemFile(**values)
            file.has_staging_collision = collision
            return file

        baseline = stream_root_fingerprint(leaf())
        variants = [
            leaf(name="other.bin"),
            leaf(size=2),
            leaf(is_dir=True),
            leaf(time_created=created + timedelta(seconds=2)),
            leaf(time_modified=modified + timedelta(seconds=2)),
            leaf(mtime_ns=2),
            leaf(is_staging=True),
            leaf(has_staging_collision=True),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(baseline, stream_root_fingerprint(variant))
