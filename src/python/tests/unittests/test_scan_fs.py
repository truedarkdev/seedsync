# Copyright 2026, SeedSync Contributors, All rights reserved.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from system import SystemFile


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
