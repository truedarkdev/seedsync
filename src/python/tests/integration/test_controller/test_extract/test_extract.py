# Copyright 2017, Inderpreet Singh, All rights reserved.

import bz2
import gzip
import io
import unittest
import shutil
import tempfile
import os
import subprocess
import tarfile
import zipfile

from common import overrides
from controller.extract import Extract, ExtractError

HAS_7Z = shutil.which("7z") is not None
HAS_RAR = shutil.which("rar") is not None and HAS_7Z


@unittest.skipUnless(HAS_7Z, "7z executable not available")
class TestExtract(unittest.TestCase):
    temp_root = None
    temp_dir = None

    ar_zip = None
    ar_rar = None
    ar_rar_split_p1 = None
    ar_rar_split_p2 = None
    ar_tar_gz = None
    ar_tar_tbz = None

    __FILE_CONTENT = "12345678"*10*1024  # 80 KB

    # For debugging
    __KEEP_TMP_FILES = False

    @classmethod
    def setUpClass(cls):
        TestExtract.temp_root = tempfile.mkdtemp(prefix="test_extract_")

        # Create a temp file to archive
        temp_file = os.path.join(TestExtract.temp_root, "file")
        with open(temp_file, "w") as f:
            f.write(TestExtract.__FILE_CONTENT)

        # Create archives
        archive_dir = os.path.join(TestExtract.temp_root, "archives")
        os.mkdir(archive_dir)

        # zip
        TestExtract.ar_zip = os.path.join(archive_dir, "file.zip")
        zf = zipfile.ZipFile(TestExtract.ar_zip, "w", zipfile.ZIP_DEFLATED)
        zf.write(temp_file, os.path.basename(temp_file))
        zf.close()

        if HAS_RAR:
            # rar
            TestExtract.ar_rar = os.path.join(archive_dir, "file.rar")
            with open(os.devnull, 'w') as fnull:
                subprocess.run(["rar",
                                "a",
                                "-ep",
                                TestExtract.ar_rar,
                                temp_file],
                               stdout=fnull,
                               check=True)

                # rar split
                subprocess.run(["rar",
                                "a",
                                "-ep", "-m0", "-v50k",
                                os.path.join(archive_dir, "file.split.rar"),
                                temp_file],
                               stdout=fnull,
                               check=True)
            TestExtract.ar_rar_split_p1 = os.path.join(archive_dir, "file.split.part1.rar")
            TestExtract.ar_rar_split_p2 = os.path.join(archive_dir, "file.split.part2.rar")

        # tar.gz
        TestExtract.ar_tar_gz = os.path.join(archive_dir, "file.tar.gz")
        subprocess.run(["tar",
                        "czvf",
                        TestExtract.ar_tar_gz,
                        "-C", os.path.dirname(temp_file),
                        os.path.basename(temp_file)],
                       check=True)

        # tbz
        TestExtract.ar_tar_tbz = os.path.join(archive_dir, "file.tbz")
        subprocess.run(["tar",
                        "cjvf",
                        TestExtract.ar_tar_tbz,
                        "-C", os.path.dirname(temp_file),
                        os.path.basename(temp_file)],
                       check=True)

    @classmethod
    def tearDownClass(cls):
        # Cleanup
        if not TestExtract.__KEEP_TMP_FILES:
            shutil.rmtree(TestExtract.temp_root)

    @overrides(unittest.TestCase)
    def setUp(self):
        TestExtract.temp_dir = os.path.join(TestExtract.temp_root, "tmp")
        os.mkdir(TestExtract.temp_dir)

    @overrides(unittest.TestCase)
    def tearDown(self):
        if not TestExtract.__KEEP_TMP_FILES:
            shutil.rmtree(TestExtract.temp_dir)

    def _assert_extracted_files(self, dir_path):
        path = os.path.join(dir_path, "file")
        self.assertTrue(os.path.isfile(path))
        with open(path, "r") as f:
            self.assertEqual(TestExtract.__FILE_CONTENT, f.read())

    def test_is_archive_fast(self):
        self.assertTrue(Extract.is_archive_fast("a.zip"))
        self.assertTrue(Extract.is_archive_fast("b.rar"))
        self.assertTrue(Extract.is_archive_fast("c.bz2"))
        self.assertTrue(Extract.is_archive_fast("d.tar.gz"))
        self.assertTrue(Extract.is_archive_fast("e.7z"))
        self.assertFalse(Extract.is_archive_fast("f.lz"))

        self.assertFalse(Extract.is_archive_fast("a"))
        self.assertFalse(Extract.is_archive_fast("a.b"))
        self.assertFalse(Extract.is_archive_fast(".b"))
        self.assertFalse(Extract.is_archive_fast(".zip"))
        self.assertFalse(Extract.is_archive_fast(""))
        self.assertFalse(Extract.is_archive_fast("7"))
        self.assertFalse(Extract.is_archive_fast("z"))

    def test_is_archive_fast_works_with_full_paths(self):
        self.assertTrue(Extract.is_archive_fast("/full/path/a.zip"))
        self.assertFalse(Extract.is_archive_fast("/full/path/a"))
        self.assertFalse(Extract.is_archive_fast("/full/path/.zip"))

    def test_is_archive_false_on_nonexisting_file(self):
        self.assertFalse(Extract.is_archive(os.path.join(TestExtract.temp_dir, "no_file")))

    def test_is_archive_false_on_dir(self):
        path = os.path.join(TestExtract.temp_dir, "dir")
        os.mkdir(path)
        self.assertTrue(os.path.isdir(path))
        self.assertFalse(Extract.is_archive(path))

    def test_is_archive_false_on_bad_archive(self):
        path = os.path.join(TestExtract.temp_dir, "bad_file")
        with open(path, 'wb') as f:
            f.write(bytearray(os.urandom(100)))
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(Extract.is_archive(path))

    def test_is_archive_zip(self):
        self.assertTrue(Extract.is_archive(TestExtract.ar_zip))

    @unittest.skipUnless(HAS_RAR, "rar and 7z executables not available")
    def test_is_archive_rar(self):
        self.assertTrue(Extract.is_archive(TestExtract.ar_rar))

    @unittest.skipUnless(HAS_RAR, "rar and 7z executables not available")
    def test_is_archive_rar_split(self):
        self.assertTrue(Extract.is_archive(TestExtract.ar_rar_split_p1))
        self.assertTrue(Extract.is_archive(TestExtract.ar_rar_split_p2))

    def test_is_archive_tar_gz(self):
        self.assertTrue(Extract.is_archive(TestExtract.ar_tar_gz))

    def test_is_archive_tar_tbz(self):
        self.assertTrue(Extract.is_archive(TestExtract.ar_tar_tbz))

    def test_extract_archive_fails_on_nonexisting_file(self):
        with self.assertRaises(ExtractError) as ctx:
            Extract.extract_archive(archive_path=os.path.join(TestExtract.temp_dir, "no_file"),
                                    out_dir_path=TestExtract.temp_dir)
        self.assertTrue(str(ctx.exception).startswith("Path is not a valid archive"))

    def test_extract_archive_fails_on_dir(self):
        with self.assertRaises(ExtractError) as ctx:
            Extract.extract_archive(archive_path=TestExtract.temp_dir,
                                    out_dir_path=TestExtract.temp_dir)
        self.assertTrue(str(ctx.exception).startswith("Path is not a valid archive"))

    def test_extract_archive_fails_on_bad_file(self):
        path = os.path.join(TestExtract.temp_dir, "bad_file")
        with open(path, 'wb') as f:
            f.write(bytearray(os.urandom(100)))
        self.assertTrue(os.path.isfile(path))
        with self.assertRaises(ExtractError) as ctx:
            Extract.extract_archive(archive_path=path,
                                    out_dir_path=TestExtract.temp_dir)
        self.assertTrue(str(ctx.exception).startswith("Path is not a valid archive"))

    @unittest.skipUnless(HAS_RAR, "rar and 7z executables not available")
    def test_extract_archive_creates_sub_directories(self):
        out_path = os.path.join(TestExtract.temp_dir, "bunch", "of", "sub", "dir")
        Extract.extract_archive(archive_path=TestExtract.ar_rar,
                                out_dir_path=out_path)
        self._assert_extracted_files(out_path)

    def test_extract_archive_zip(self):
        Extract.extract_archive(archive_path=TestExtract.ar_zip,
                                out_dir_path=TestExtract.temp_dir)
        self._assert_extracted_files(TestExtract.temp_dir)

    def test_extract_archive_overwrites_existing(self):
        path = os.path.join(TestExtract.temp_dir, "file")
        with open(path, "w") as f:
            f.write("Dummy file")
        Extract.extract_archive(archive_path=TestExtract.ar_zip,
                                out_dir_path=TestExtract.temp_dir)
        self._assert_extracted_files(TestExtract.temp_dir)

    @unittest.skipUnless(HAS_RAR, "rar and 7z executables not available")
    def test_extract_archive_rar(self):
        Extract.extract_archive(archive_path=TestExtract.ar_rar,
                                out_dir_path=TestExtract.temp_dir)
        self._assert_extracted_files(TestExtract.temp_dir)

    @unittest.skipUnless(HAS_RAR, "rar and 7z executables not available")
    def test_extract_archive_rar_split(self):
        Extract.extract_archive(archive_path=TestExtract.ar_rar_split_p1,
                                out_dir_path=TestExtract.temp_dir)
        self._assert_extracted_files(TestExtract.temp_dir)

    def test_extract_archive_tar_gz(self):
        Extract.extract_archive(archive_path=TestExtract.ar_tar_gz,
                                out_dir_path=TestExtract.temp_dir)
        self._assert_extracted_files(TestExtract.temp_dir)

    def test_extract_archive_tar_tbz(self):
        Extract.extract_archive(archive_path=TestExtract.ar_tar_tbz,
                                out_dir_path=TestExtract.temp_dir)
        self._assert_extracted_files(TestExtract.temp_dir)

    def test_7z_zip_extraction_normalizes_traversal_members_into_output_root(self):
        archive_path = os.path.join(TestExtract.temp_dir, "traversal.zip")
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("../escape.txt", "bad")
            zf.writestr("/abs.txt", "bad2")

        subprocess.run(["7z", "x", "-y", "-o{}".format(TestExtract.temp_dir), "--", archive_path],
                       check=True,
                       stdout=subprocess.DEVNULL)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "escape.txt")))
        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "abs.txt")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "escape.txt")))
        self.assertFalse(os.path.exists("/abs.txt"))

    def test_extract_archive_7z_normalizes_traversal_members_into_output_root(self):
        archive_path = os.path.join(TestExtract.temp_dir, "traversal.7z")
        source_path = os.path.join(TestExtract.temp_dir, "payload.txt")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("bad")

        subprocess.run(["7z", "a", archive_path, source_path], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["7z", "rn", archive_path, "payload.txt", "../escape.txt"], check=True, stdout=subprocess.DEVNULL)

        Extract.extract_archive(archive_path=archive_path,
                                out_dir_path=TestExtract.temp_dir)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "escape.txt")))

    @unittest.skipUnless(HAS_RAR, "rar and 7z executables not available")
    def test_7z_rar_extraction_normalizes_traversal_members_into_output_root(self):
        archive_path = os.path.join(TestExtract.temp_dir, "traversal.rar")
        source_name = "payload.txt"
        source_path = os.path.join(TestExtract.temp_dir, source_name)
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("bad")

        subprocess.run(["rar", "a", archive_path, source_name],
                       check=True,
                       stdout=subprocess.DEVNULL,
                       cwd=TestExtract.temp_dir)
        subprocess.run(["rar", "rn", archive_path, source_name, "../escape.txt"],
                       check=True,
                       stdout=subprocess.DEVNULL,
                       cwd=TestExtract.temp_dir)
        subprocess.run(["7z", "x", "-y", "-o{}".format(TestExtract.temp_dir), "--", archive_path],
                       check=True,
                       stdout=subprocess.DEVNULL)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "escape.txt")))

    def test_7z_gzip_extraction_writes_only_single_output_file_in_output_root(self):
        archive_path = os.path.join(TestExtract.temp_dir, "single.gz")
        with gzip.open(archive_path, "wb") as handle:
            handle.write(b"ok")

        subprocess.run(["7z", "x", "-y", "-o{}".format(TestExtract.temp_dir), "--", archive_path],
                       check=True,
                       stdout=subprocess.DEVNULL)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "single")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "single")))

    def test_extract_archive_gzip_header_filename_does_not_control_output_path(self):
        archive_path = os.path.join(TestExtract.temp_dir, "single.gz")
        with open(archive_path, "wb") as raw_handle:
            with gzip.GzipFile(filename="../escape.txt", mode="wb", fileobj=raw_handle) as gz_handle:
                gz_handle.write(b"ok")

        Extract.extract_archive(archive_path=archive_path,
                                out_dir_path=TestExtract.temp_dir)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "single")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_dir, "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "escape.txt")))

    def test_7z_bzip2_extraction_writes_only_single_output_file_in_output_root(self):
        archive_path = os.path.join(TestExtract.temp_dir, "single.bz2")
        with bz2.open(archive_path, "wb") as handle:
            handle.write(b"ok")

        subprocess.run(["7z", "x", "-y", "-o{}".format(TestExtract.temp_dir), "--", archive_path],
                       check=True,
                       stdout=subprocess.DEVNULL)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "single")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "single")))

    def test_7z_wrapped_tar_outer_pass_writes_tar_only_inside_output_root(self):
        archive_path = os.path.join(TestExtract.temp_dir, "outer.tbz")
        payload_path = os.path.join(TestExtract.temp_dir, "file.txt")
        with open(payload_path, "w", encoding="utf-8") as handle:
            handle.write("ok")
        subprocess.run(["tar",
                        "cjvf",
                        archive_path,
                        "-C", TestExtract.temp_dir,
                        os.path.basename(payload_path)],
                       check=True,
                       stdout=subprocess.DEVNULL)

        wrapped_root = os.path.join(TestExtract.temp_dir, "wrapped")
        os.mkdir(wrapped_root)
        subprocess.run(["7z", "x", "-y", "-o{}".format(wrapped_root), "--", archive_path],
                       check=True,
                       stdout=subprocess.DEVNULL)

        self.assertTrue(os.path.isfile(os.path.join(wrapped_root, "outer.tar")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "outer.tar")))

    def test_extract_archive_tgz_header_filename_does_not_control_outer_tar_path(self):
        archive_path = os.path.join(TestExtract.temp_dir, "outer.tgz")
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tf:
            data = b"ok"
            info = tarfile.TarInfo("nested/file.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with open(archive_path, "wb") as raw_handle:
            with gzip.GzipFile(filename="../escape.tar", mode="wb", fileobj=raw_handle) as gz_handle:
                gz_handle.write(tar_buffer.getvalue())

        Extract.extract_archive(archive_path=archive_path,
                                out_dir_path=TestExtract.temp_dir)

        self.assertTrue(os.path.isfile(os.path.join(TestExtract.temp_dir, "nested", "file.txt")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_dir, "escape.tar")))
        self.assertFalse(os.path.exists(os.path.join(TestExtract.temp_root, "escape.tar")))
