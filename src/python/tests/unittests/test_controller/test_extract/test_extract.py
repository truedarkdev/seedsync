import bz2
import io
import gzip
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from controller.extract.extract import Extract, ExtractError


class TestExtractArchiveSecurity(unittest.TestCase):
    def _write_zip(self, archive_path: str, members):
        with zipfile.ZipFile(archive_path, "w") as zf:
            for name, content in members:
                zf.writestr(name, content)

    def _write_tar_gz(self, archive_path: str, members):
        with tarfile.open(archive_path, "w:gz") as tf:
            for name, content in members:
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

    def _write_gzip(self, archive_path: str, content: str):
        with gzip.open(archive_path, "wb") as handle:
            handle.write(content.encode("utf-8"))

    def _write_bz2(self, archive_path: str, content: str):
        with bz2.open(archive_path, "wb") as handle:
            handle.write(content.encode("utf-8"))

    def _write_signature_archive(self, archive_path: str, signature: bytes):
        with open(archive_path, "wb") as handle:
            handle.write(signature)
            handle.write(b"\0\0")

    def _assert_suppressed_subprocess(self, run):
        kwargs = run.call_args.kwargs
        self.assertNotIn("capture_output", kwargs)
        self.assertNotIn("text", kwargs)
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdout"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stderr"])
        self.assertFalse(kwargs["check"])
        self.assertEqual(Extract._Extract__SUBPROCESS_TIMEOUT_SECS, kwargs["timeout"])

    def test_zip_member_traversal_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "out")
            self._write_zip(archive_path, [("../escape.txt", "bad")])

            with patch.object(zipfile.ZipFile, "extractall") as extractall:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            extractall.assert_not_called()

    def test_zip_symlink_member_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "out")
            with zipfile.ZipFile(archive_path, "w") as zf:
                info = zipfile.ZipInfo("linked")
                info.external_attr = 0xA000 << 16
                zf.writestr(info, "target")

            with patch.object(zipfile.ZipFile, "extractall") as extractall:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            extractall.assert_not_called()

    def test_tar_hardlink_member_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tar")
            out_dir = os.path.join(temp_dir, "out")
            with tarfile.open(archive_path, "w") as tf:
                info = tarfile.TarInfo("linked")
                info.type = tarfile.LNKTYPE
                info.linkname = "../target"
                tf.addfile(info)

            with patch("controller.extract.extract.subprocess.run") as run:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            run.assert_not_called()

    def test_normal_zip_extracts_after_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "out")
            self._write_zip(archive_path, [("nested/file.txt", "ok")])

            Extract.extract_archive(archive_path, out_dir)

            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_tar_gz_extracts_after_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tar.gz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_tar_gz(archive_path, [("nested/file.txt", "ok")])

            Extract.extract_archive(archive_path, out_dir)

            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_gzip_extracts_single_file_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.gz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_gzip(archive_path, "ok")
            self.assertTrue(Extract.is_archive(archive_path))

            Extract.extract_archive(archive_path, out_dir)

            with open(os.path.join(out_dir, "archive"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_bzip2_extracts_single_file_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.bz2")
            out_dir = os.path.join(temp_dir, "out")
            self._write_bz2(archive_path, "ok")
            self.assertTrue(Extract.is_archive(archive_path))

            Extract.extract_archive(archive_path, out_dir)

            with open(os.path.join(out_dir, "archive"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_rar_extracts_through_staged_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            def _extract_archive(command, **_kwargs):
                payload_root = command[-1]
                os.makedirs(os.path.join(payload_root, "nested"))
                with open(os.path.join(payload_root, "nested", "file.txt"), "w", encoding="utf-8") as handle:
                    handle.write("ok")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("controller.extract.extract.subprocess.run", side_effect=_extract_archive) as run:
                Extract.extract_archive(archive_path, out_dir)

            run.assert_called_once()
            self._assert_suppressed_subprocess(run)
            self.assertEqual("unrar", run.call_args.args[0][0])
            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_rar_subprocess_escape_is_rejected_before_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            def _extract_archive(command, **_kwargs):
                temp_root = os.path.dirname(command[-1])
                with open(os.path.join(temp_root, "escape.txt"), "w", encoding="utf-8") as handle:
                    handle.write("bad")
                with open(os.path.join(command[-1], "safe.txt"), "w", encoding="utf-8") as handle:
                    handle.write("should not merge")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("controller.extract.extract.subprocess.run", side_effect=_extract_archive):
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            self.assertFalse(os.path.exists(os.path.join(out_dir, "safe.txt")))

    def test_rar_missing_tool_raises_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            with patch("controller.extract.extract.subprocess.run", side_effect=FileNotFoundError()):
                with self.assertRaises(ExtractError) as ctx:
                    Extract.extract_archive(archive_path, out_dir)

            self.assertEqual("Required extraction tool not found: unrar", str(ctx.exception))

    def test_7z_subprocess_failure_raises_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.7z")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"7z\xbc\xaf'\x1c")
            self.assertTrue(Extract.is_archive(archive_path))
            result = subprocess.CompletedProcess(["7z", "x"], 2, stdout="", stderr="boom\n" + ("x" * 2000))

            with patch("controller.extract.extract.subprocess.run", return_value=result) as run:
                with self.assertRaises(ExtractError) as ctx:
                    Extract.extract_archive(archive_path, out_dir)

            run.assert_called_once()
            self.assertIn("7z failed with exit code 2", str(ctx.exception))
            self._assert_suppressed_subprocess(run)
            self.assertNotIn("boom", str(ctx.exception))
            self.assertNotIn("\n", str(ctx.exception))

    def test_7z_subprocess_timeout_raises_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.7z")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"7z\xbc\xaf'\x1c")

            with patch("controller.extract.extract.subprocess.run", side_effect=subprocess.TimeoutExpired(["7z"], 300)):
                with self.assertRaises(ExtractError) as ctx:
                    Extract.extract_archive(archive_path, out_dir)

            self.assertEqual("7z failed after 300s timeout", str(ctx.exception))

    def test_extract_archive_fails_on_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.lz")
            out_dir = os.path.join(temp_dir, "out")
            with open(archive_path, "wb") as handle:
                handle.write(b"unsupported")

            with self.assertRaises(ExtractError) as ctx:
                Extract.extract_archive(archive_path, out_dir)

            self.assertTrue(str(ctx.exception).startswith("Path is not a valid archive"))

    def test_is_archive_fast_does_not_advertise_lz(self):
        self.assertFalse(Extract.is_archive_fast("archive.lz"))
