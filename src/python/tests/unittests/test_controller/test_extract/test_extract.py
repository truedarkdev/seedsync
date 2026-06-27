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
    @staticmethod
    def _extract_output_root(command):
        for arg in command:
            if arg.startswith("-o"):
                return arg[2:]
        raise AssertionError("7z output root argument not found")

    def _materialize_7z_payload(self, command, members=None, **_kwargs):
        output_root = self._extract_output_root(command)
        members = members or [("nested/file.txt", "ok")]
        for relative_path, content in members:
            full_path = os.path.join(output_root, relative_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as handle:
                handle.write(content)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    @staticmethod
    def _write_bytes_to_stream(command, payload_bytes: bytes, **kwargs):
        stdout_handle = kwargs["stdout"]
        stdout_handle.write(payload_bytes)
        stdout_handle.flush()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _materialize_wrapped_tar_then_payload(self, command, **_kwargs):
        archive_path = command[-1]
        if "-so" in command and archive_path.endswith((".tar.gz", ".tgz", ".tar.bz", ".tar.bz2", ".tbz", ".tbz2")):
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tf:
                data = b"ok"
                info = tarfile.TarInfo("nested/file.txt")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            return self._write_bytes_to_stream(command, tar_buffer.getvalue(), **_kwargs)
        return self._materialize_7z_payload(command)

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

    def _write_tar_bz2(self, archive_path: str, members):
        with tarfile.open(archive_path, "w:bz2") as tf:
            for name, content in members:
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

    def _write_gzip(self, archive_path: str, content: str):
        with gzip.open(archive_path, "wb") as handle:
            handle.write(content.encode("utf-8"))

    def _write_gzip_with_header_name(self, archive_path: str, content: str, header_name: str):
        with open(archive_path, "wb") as raw_handle:
            with gzip.GzipFile(filename=header_name, mode="wb", fileobj=raw_handle) as gz_handle:
                gz_handle.write(content.encode("utf-8"))

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

    def test_tar_fifo_member_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tar")
            out_dir = os.path.join(temp_dir, "out")
            with tarfile.open(archive_path, "w") as tf:
                info = tarfile.TarInfo("fifo")
                info.type = tarfile.FIFOTYPE
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

            with patch("controller.extract.extract.subprocess.run", side_effect=self._materialize_7z_payload) as run:
                Extract.extract_archive(archive_path, out_dir)

            command = run.call_args.args[0]
            self.assertEqual(["7z", "x", "-y"], command[:3])
            self.assertEqual("--", command[-2])
            self.assertEqual(archive_path, command[-1])
            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_tar_gz_extracts_after_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tar.gz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_tar_gz(archive_path, [("nested/file.txt", "ok")])

            with patch("controller.extract.extract.subprocess.run", side_effect=self._materialize_wrapped_tar_then_payload) as run:
                Extract.extract_archive(archive_path, out_dir)

            self.assertEqual(2, run.call_count)
            first_command = run.call_args_list[0].args[0]
            second_command = run.call_args_list[1].args[0]
            self.assertEqual(["7z", "x", "-so"], first_command[:3])
            self.assertEqual("--", first_command[-2])
            self.assertEqual(archive_path, first_command[-1])
            self.assertEqual("--", second_command[-2])
            self.assertTrue(second_command[-1].endswith("archive.tar"))
            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_tar_tbz_extracts_after_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tbz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_tar_bz2(archive_path, [("nested/file.txt", "ok")])

            with patch("controller.extract.extract.subprocess.run", side_effect=self._materialize_wrapped_tar_then_payload) as run:
                Extract.extract_archive(archive_path, out_dir)

            self.assertEqual(2, run.call_count)
            first_command = run.call_args_list[0].args[0]
            second_command = run.call_args_list[1].args[0]
            self.assertEqual(["7z", "x", "-so"], first_command[:3])
            self.assertEqual("--", first_command[-2])
            self.assertEqual(archive_path, first_command[-1])
            self.assertEqual("--", second_command[-2])
            self.assertTrue(second_command[-1].endswith("archive.tar"))
            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_wrapped_tar_first_pass_write_outside_payload_root_is_rejected_before_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tbz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_tar_bz2(archive_path, [("nested/file.txt", "ok")])

            def _extract_archive(command, **kwargs):
                if command[-1].endswith(".tbz"):
                    wrapped_tar_path = kwargs["stdout"].name
                    temp_root = os.path.dirname(os.path.dirname(wrapped_tar_path))
                    with open(os.path.join(temp_root, "escape.txt"), "w", encoding="utf-8") as handle:
                        handle.write("bad")
                return self._materialize_wrapped_tar_then_payload(command, **kwargs)

            with patch("controller.extract.extract.subprocess.run", side_effect=_extract_archive):
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            self.assertFalse(os.path.exists(os.path.join(out_dir, "nested", "file.txt")))

    def test_gzip_extracts_single_file_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.gz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_gzip(archive_path, "ok")
            self.assertTrue(Extract.is_archive(archive_path))

            with patch(
                "controller.extract.extract.subprocess.run",
                side_effect=lambda command, **kwargs: self._write_bytes_to_stream(command, b"ok", **kwargs),
            ) as run:
                Extract.extract_archive(archive_path, out_dir)

            command = run.call_args.args[0]
            self.assertEqual(["7z", "x", "-so"], command[:3])
            self.assertEqual("--", command[-2])
            self.assertEqual(archive_path, command[-1])
            with open(os.path.join(out_dir, "archive"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_bzip2_extracts_single_file_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.bz2")
            out_dir = os.path.join(temp_dir, "out")
            self._write_bz2(archive_path, "ok")
            self.assertTrue(Extract.is_archive(archive_path))

            with patch(
                "controller.extract.extract.subprocess.run",
                side_effect=lambda command, **kwargs: self._write_bytes_to_stream(command, b"ok", **kwargs),
            ) as run:
                Extract.extract_archive(archive_path, out_dir)

            command = run.call_args.args[0]
            self.assertEqual(["7z", "x", "-so"], command[:3])
            self.assertEqual("--", command[-2])
            self.assertEqual(archive_path, command[-1])
            with open(os.path.join(out_dir, "archive"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_gzip_header_filename_does_not_control_output_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.gz")
            out_dir = os.path.join(temp_dir, "out")
            self._write_gzip_with_header_name(archive_path, "ok", "../escape.txt")

            with patch(
                "controller.extract.extract.subprocess.run",
                side_effect=lambda command, **kwargs: self._write_bytes_to_stream(command, b"ok", **kwargs),
            ):
                Extract.extract_archive(archive_path, out_dir)

            self.assertTrue(os.path.isfile(os.path.join(out_dir, "archive")))
            self.assertFalse(os.path.exists(os.path.join(out_dir, "escape.txt")))

    def test_rar_extracts_through_7z_staged_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            def _extract_archive(command, **_kwargs):
                return self._materialize_7z_payload(command)

            with patch("controller.extract.extract.subprocess.run", side_effect=_extract_archive) as run:
                Extract.extract_archive(archive_path, out_dir)

            run.assert_called_once()
            self._assert_suppressed_subprocess(run)
            command = run.call_args.args[0]
            self.assertEqual("7z", command[0])
            self.assertEqual("--", command[-2])
            self.assertEqual(archive_path, command[-1])
            with open(os.path.join(out_dir, "nested", "file.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual("ok", handle.read())

    def test_rar_subprocess_hardlink_is_rejected_before_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            def _extract_archive(command, **_kwargs):
                payload_root = self._extract_output_root(command)
                temp_root = os.path.dirname(payload_root)
                outside_path = os.path.join(temp_root, "outside.txt")
                with open(outside_path, "w", encoding="utf-8") as handle:
                    handle.write("bad")
                os.link(outside_path, os.path.join(payload_root, "linked.txt"))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("controller.extract.extract.subprocess.run", side_effect=_extract_archive):
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            self.assertFalse(os.path.exists(os.path.join(out_dir, "linked.txt")))

    def test_rar_subprocess_write_outside_payload_root_is_rejected_before_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            def _extract_archive(command, **_kwargs):
                payload_root = self._extract_output_root(command)
                temp_root = os.path.dirname(payload_root)
                with open(os.path.join(temp_root, "escape.txt"), "w", encoding="utf-8") as handle:
                    handle.write("bad")
                return self._materialize_7z_payload(command)

            with patch("controller.extract.extract.subprocess.run", side_effect=_extract_archive):
                with self.assertRaises(ExtractError):
                    Extract.extract_archive(archive_path, out_dir)

            self.assertFalse(os.path.exists(os.path.join(out_dir, "nested", "file.txt")))

    def test_rar_missing_tool_raises_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.rar")
            out_dir = os.path.join(temp_dir, "out")
            self._write_signature_archive(archive_path, b"Rar!\x1a\x07\x00")
            self.assertTrue(Extract.is_archive(archive_path))

            with patch("controller.extract.extract.subprocess.run", side_effect=FileNotFoundError()):
                with self.assertRaises(ExtractError) as ctx:
                    Extract.extract_archive(archive_path, out_dir)

            self.assertEqual("Required extraction tool not found: 7z", str(ctx.exception))

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

    def test_tar_parser_error_is_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.tar")
            out_dir = os.path.join(temp_dir, "out")
            with tarfile.open(archive_path, "w") as tf:
                data = b"ok"
                info = tarfile.TarInfo("file.txt")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

            with patch("controller.extract.extract.tarfile.is_tarfile", return_value=True):
                with patch("controller.extract.extract.tarfile.open", side_effect=tarfile.TarError("bad tar")):
                    with patch("controller.extract.extract.subprocess.run") as run:
                        with self.assertRaises(ExtractError) as ctx:
                            Extract.extract_archive(archive_path, out_dir)

            self.assertEqual("bad tar", str(ctx.exception))
            run.assert_not_called()

    def test_temp_extract_root_uses_output_parent_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.zip")
            out_dir = os.path.join(temp_dir, "nested", "out")
            self._write_zip(archive_path, [("nested/file.txt", "ok")])

            with patch("controller.extract.extract.subprocess.run", side_effect=self._materialize_7z_payload):
                with patch("controller.extract.extract.tempfile.TemporaryDirectory", wraps=tempfile.TemporaryDirectory) as tempdir:
                    Extract.extract_archive(archive_path, out_dir)

            self.assertEqual(
                os.path.dirname(os.path.realpath(out_dir)),
                tempdir.call_args.kwargs["dir"],
            )

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

    def test_is_archive_fast_advertises_tbz(self):
        self.assertTrue(Extract.is_archive_fast("archive.tbz"))
