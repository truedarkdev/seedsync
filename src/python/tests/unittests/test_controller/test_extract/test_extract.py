import os
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

    def test_zip_member_traversal_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "out")
            self._write_zip(archive_path, [("../escape.txt", "bad")])

            with patch.object(Extract, "is_archive", return_value=True), \
                    patch("controller.extract.extract.patoolib.extract_archive") as extract_archive:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            extract_archive.assert_not_called()

    def test_zip_symlink_member_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "out")
            with zipfile.ZipFile(archive_path, "w") as zf:
                info = zipfile.ZipInfo("linked")
                info.external_attr = 0xA000 << 16
                zf.writestr(info, "target")

            with patch.object(Extract, "is_archive", return_value=True), \
                    patch("controller.extract.extract.patoolib.extract_archive") as extract_archive:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            extract_archive.assert_not_called()

    def test_tar_hardlink_member_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tar")
            out_dir = os.path.join(temp_dir, "out")
            with tarfile.open(archive_path, "w") as tf:
                info = tarfile.TarInfo("linked")
                info.type = tarfile.LNKTYPE
                info.linkname = "../target"
                tf.addfile(info)

            with patch.object(Extract, "is_archive", return_value=True), \
                    patch("controller.extract.extract.patoolib.extract_archive") as extract_archive:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            extract_archive.assert_not_called()

    def test_non_prevalidated_archive_extracts_through_staged_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.7z")
            out_dir = os.path.join(temp_dir, "out")
            with open(archive_path, "w") as f:
                f.write("not really 7z")

            def _extract_archive(_archive_path, outdir, interactive):
                os.makedirs(os.path.join(outdir, "nested"))
                with open(os.path.join(outdir, "nested", "file.txt"), "w") as f:
                    f.write("ok")

            with patch.object(Extract, "is_archive", return_value=True), \
                    patch("controller.extract.extract.zipfile.is_zipfile", return_value=False), \
                    patch("controller.extract.extract.tarfile.open", side_effect=tarfile.TarError("bad")), \
                    patch("controller.extract.extract.patoolib.extract_archive", side_effect=_extract_archive) as extract_archive:
                Extract.extract_archive(archive_path, out_dir)

            extract_archive.assert_called_once()
            with open(os.path.join(out_dir, "nested", "file.txt"), "r") as f:
                self.assertEqual("ok", f.read())

    def test_non_prevalidated_archive_rejects_payload_escape_before_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            with open(archive_path, "w") as f:
                f.write("not really rar")

            def _extract_archive(_archive_path, outdir, interactive):
                with open(os.path.join(os.path.dirname(outdir), "escape.txt"), "w") as f:
                    f.write("bad")
                with open(os.path.join(outdir, "safe.txt"), "w") as f:
                    f.write("should not merge")

            with patch.object(Extract, "is_archive", return_value=True), \
                    patch("controller.extract.extract.zipfile.is_zipfile", return_value=False), \
                    patch("controller.extract.extract.tarfile.open", side_effect=tarfile.TarError("bad")), \
                    patch("controller.extract.extract.patoolib.extract_archive", side_effect=_extract_archive) as extract_archive:
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            extract_archive.assert_called_once()
            self.assertFalse(os.path.exists(os.path.join(out_dir, "safe.txt")))

    def test_normal_zip_extracts_after_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "out")
            self._write_zip(archive_path, [("nested/file.txt", "ok")])

            def _extract_archive(_archive_path, outdir, interactive):
                os.makedirs(os.path.join(outdir, "nested"))
                with open(os.path.join(outdir, "nested", "file.txt"), "w") as f:
                    f.write("ok")

            with patch.object(Extract, "is_archive", return_value=True), \
                    patch("controller.extract.extract.patoolib.extract_archive", side_effect=_extract_archive) as extract_archive:
                Extract.extract_archive(archive_path, out_dir)

            extract_archive.assert_called_once()
            self.assertNotEqual(out_dir, extract_archive.call_args.kwargs["outdir"])
            with open(os.path.join(out_dir, "nested", "file.txt"), "r") as f:
                self.assertEqual("ok", f.read())
