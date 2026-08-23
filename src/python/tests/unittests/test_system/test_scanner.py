# Copyright 2017, Inderpreet Singh, All rights reserved.

import errno
import os
import shutil
import tempfile
import unittest
from threading import Thread
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from common.lftp_status import parse_lftp_pget_status_bytes
from system import SystemScanner, SystemScannerError
from system.scanner import _record_lftp_sidecar_breadcrumb, lftp_sidecar_target_identity


def my_mkdir(*args):
    os.mkdir(os.path.join(TestSystemScanner.temp_dir, *args))


def my_touch(size, *args):
    path = os.path.join(TestSystemScanner.temp_dir, *args)
    with open(path, 'wb') as f:
        f.write(bytearray([0xff] * size))


def my_mkdir_latin(*args):
    os.mkdir(os.path.join(TestSystemScanner.temp_dir.encode('latin-1'), *args))


def my_touch_latin(size, *args):
    path = os.path.join(TestSystemScanner.temp_dir.encode('latin-1'), *args)
    with open(path, 'wb') as f:
        f.write(bytearray([0xff] * size))


# noinspection SpellCheckingInspection
class TestSystemScanner(unittest.TestCase):
    temp_dir = None

    def setUp(self):
        # Create a temp directory
        TestSystemScanner.temp_dir = tempfile.mkdtemp(prefix="test_system_scanner")

    def tearDown(self):
        # Cleanup
        shutil.rmtree(TestSystemScanner.temp_dir)

    def setup_default_tree(self):
        # Create a bunch files and directories
        # a [dir]
        #   aa [dir]
        #      .aaa [dir]
        #      .aab [file, 512 bytes]
        #   ab [file, 12*1024 + 4 bytes]
        # b [dir]
        #   ba [dir]
        #      baa [file, 512 + 7 bytes]
        #   bb [dir]
        #     bba [dir]
        #     bbb [file, 24*1024*1024 + 24 bytes]
        #     bbc [dir]
        #        bbca [dir]
        #           .bbcaa [file, 1 byte
        # c [file, 1234 bytes]
        my_mkdir("a")
        my_mkdir("a", "aa")
        my_mkdir("a", "aa", ".aaa")
        my_touch(512, "a", "aa", ".aab")
        my_touch(12*1024+4, "a", "ab")
        my_mkdir("b")
        my_mkdir("b", "ba")
        my_touch(512+7, "b", "ba", "baa")
        my_mkdir("b", "bb")
        my_mkdir("b", "bb", "bba")
        my_touch(24*1024*1024+24, "b", "bb", "bbb")
        my_mkdir("b", "bb", "bbc")
        my_mkdir("b", "bb", "bbc", "bbca")
        my_touch(1, "b", "bb", "bbc", "bbca", ".bbcaa")
        my_touch(1234, "c")

    def test_scan_tree(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)

        self.assertEqual("a", a.name)
        self.assertTrue(a.is_dir)
        self.assertEqual("b", b.name)
        self.assertTrue(b.is_dir)
        self.assertEqual("c", c.name)
        self.assertFalse(c.is_dir)
        self.assertEqual(os.stat(os.path.join(TestSystemScanner.temp_dir, "c")).st_mtime_ns, c.mtime_ns)

        self.assertEqual(2, len(a.children))
        aa, ab = tuple(a.children)
        self.assertEqual("aa", aa.name)
        self.assertTrue(aa.is_dir)
        self.assertEqual(2, len(aa.children))
        aaa, aab = tuple(aa.children)
        self.assertEqual(".aaa", aaa.name)
        self.assertTrue(aaa.is_dir)
        self.assertEqual(".aab", aab.name)
        self.assertFalse(aab.is_dir)
        self.assertEqual("ab", ab.name)
        self.assertFalse(ab.is_dir)

        self.assertEqual(2, len(b.children))
        ba, bb = tuple(b.children)
        self.assertEqual("ba", ba.name)
        self.assertTrue(ba.is_dir)
        self.assertEqual(1, len(ba.children))
        baa = ba.children[0]
        self.assertEqual("baa", baa.name)
        self.assertFalse(baa.is_dir)
        self.assertEqual("bb", bb.name)
        self.assertTrue(bb.is_dir)
        self.assertEqual(3, len(bb.children))
        bba, bbb, bbc = tuple(bb.children)
        self.assertEqual("bba", bba.name)
        self.assertTrue(bba.is_dir)
        self.assertEqual("bbb", bbb.name)
        self.assertFalse(bbb.is_dir)
        self.assertEqual("bbc", bbc.name)
        self.assertTrue(bbc.is_dir)
        self.assertEqual(1, len(bbc.children))
        bbca = bbc.children[0]
        self.assertEqual("bbca", bbca.name)
        self.assertTrue(bbca.is_dir)
        self.assertEqual(1, len(bbca.children))
        bbcaa = bbca.children[0]
        self.assertEqual(".bbcaa", bbcaa.name)
        self.assertFalse(bbcaa.is_dir)

    def test_scan_size(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)
        aa, ab = tuple(a.children)
        aaa, aab = tuple(aa.children)
        ba, bb = tuple(b.children)
        baa = ba.children[0]
        bba, bbb, bbc = tuple(bb.children)
        bbca = bbc.children[0]
        bbcaa = bbca.children[0]

        self.assertEqual(12*1024+4+512, a.size)
        self.assertEqual(512, aa.size)
        self.assertEqual(0, aaa.size)
        self.assertEqual(512, aab.size)
        self.assertEqual(12*1024+4, ab.size)
        self.assertEqual(512+7+24*1024*1024+24+1, b.size)
        self.assertEqual(512+7, ba.size)
        self.assertEqual(512+7, baa.size)
        self.assertEqual(24*1024*1024+24+1, bb.size)
        self.assertEqual(0, bba.size)
        self.assertEqual(24*1024*1024+24, bbb.size)
        self.assertEqual(1, bbc.size)
        self.assertEqual(1, bbca.size)
        self.assertEqual(1, bbcaa.size)
        self.assertEqual(1234, c.size)

    def test_scan_non_existing_dir_fails(self):
        self.setup_default_tree()
        scanner = SystemScanner(
            path_to_scan=os.path.join(TestSystemScanner.temp_dir, "nonexisting")
        )
        with self.assertRaises(SystemScannerError) as ex:
            scanner.scan()
        self.assertTrue(str(ex.exception).startswith("Path does not exist"))

    def test_scan_file_fails(self):
        self.setup_default_tree()
        scanner = SystemScanner(
            path_to_scan=os.path.join(TestSystemScanner.temp_dir, "c")
        )
        with self.assertRaises(SystemScannerError) as ex:
            scanner.scan()
        self.assertTrue(str(ex.exception).startswith("Path is not a directory"))

    def test_scan_single_dir(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        a = scanner.scan_single("a")

        self.assertEqual("a", a.name)
        self.assertTrue(a.is_dir)

        self.assertEqual(2, len(a.children))
        aa, ab = tuple(a.children)
        self.assertEqual("aa", aa.name)
        self.assertTrue(aa.is_dir)
        self.assertEqual(2, len(aa.children))
        aaa, aab = tuple(aa.children)
        self.assertEqual(".aaa", aaa.name)
        self.assertTrue(aaa.is_dir)
        self.assertEqual(".aab", aab.name)
        self.assertFalse(aab.is_dir)
        self.assertEqual("ab", ab.name)
        self.assertFalse(ab.is_dir)

        self.assertEqual(12*1024+4+512, a.size)
        self.assertEqual(512, aa.size)
        self.assertEqual(0, aaa.size)
        self.assertEqual(512, aab.size)
        self.assertEqual(12*1024+4, ab.size)

    def test_scan_single_file(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        c = scanner.scan_single("c")

        self.assertEqual("c", c.name)
        self.assertFalse(c.is_dir)
        self.assertEqual(1234, c.size)

    def test_scan_single_non_existing_path_fails(self):
        self.setup_default_tree()
        scanner = SystemScanner(
            path_to_scan=os.path.join(TestSystemScanner.temp_dir)
        )
        with self.assertRaises(SystemScannerError) as ex:
            scanner.scan_single("nonexisting")
        self.assertTrue(str(ex.exception).startswith("Path does not exist"))

    def test_scan_single_converts_stat_race_to_scanner_error(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)

        with patch.object(scanner, "_SystemScanner__create_system_file", side_effect=FileNotFoundError("gone")):
            with self.assertRaises(SystemScannerError) as ex:
                scanner.scan_single("c")

        self.assertIn("Failed to scan", str(ex.exception))

    def test_scan_single_preserves_permission_error(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        error = PermissionError("denied")

        with patch.object(scanner, "_SystemScanner__create_system_file", side_effect=error):
            with self.assertRaises(PermissionError) as ex:
                scanner.scan_single("c")

        self.assertIs(error, ex.exception)

    def test_scan_single_preserves_non_race_os_error(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        error = OSError(errno.EIO, "I/O error")

        with patch.object(scanner, "_SystemScanner__create_system_file", side_effect=error):
            with self.assertRaises(OSError) as ex:
                scanner.scan_single("c")

        self.assertIs(error, ex.exception)

    def test_scan_tree_excluded_prefix(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        scanner.add_exclude_prefix(".")
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)
        aa, ab = tuple(a.children)
        ba, bb = tuple(b.children)
        bba, bbb, bbc = tuple(bb.children)
        bbca = bbc.children[0]
        self.assertEqual(0, len(aa.children))
        self.assertEqual(0, len(bbca.children))

        scanner.add_exclude_prefix("ab")
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)
        self.assertEqual(1, len(a.children))
        aa = a.children[0]
        ba, bb = tuple(b.children)
        bba, bbb, bbc = tuple(bb.children)
        bbca = bbc.children[0]
        self.assertEqual("aa", aa.name)
        self.assertEqual(0, len(bbca.children))

    def test_scan_size_excluded_prefix(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        scanner.add_exclude_prefix(".")
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)
        aa, ab = tuple(a.children)
        ba, bb = tuple(b.children)
        bba, bbb, bbc = tuple(bb.children)
        bbca = bbc.children[0]
        self.assertEqual(12*1024+4, a.size)
        self.assertEqual(0, aa.size)
        self.assertEqual(24*1024*1024+24+0, bb.size)
        self.assertEqual(0, bbc.size)
        self.assertEqual(0, bbca.size)

        scanner.add_exclude_prefix("ab")
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)
        self.assertEqual(1, len(a.children))
        aa = a.children[0]
        self.assertEqual("aa", aa.name)
        self.assertEqual(0, a.size)
        self.assertEqual(0, aa.size)

    def test_scan_tree_excluded_suffix(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)

        scanner.add_exclude_suffix("ab")
        scanner.add_exclude_suffix("bb")
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)
        self.assertEqual(1, len(a.children))
        aa = a.children[0]
        self.assertEqual("aa", aa.name)
        self.assertEqual(1, len(aa.children))
        aaa = aa.children[0]
        self.assertEqual(".aaa", aaa.name)
        self.assertEqual(1, len(b.children))
        ba = b.children[0]
        self.assertEqual("ba", ba.name)

    def test_scan_size_excluded_suffix(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)

        scanner.add_exclude_suffix("ab")
        scanner.add_exclude_suffix("bb")
        files = scanner.scan()
        a, b, c = tuple(files)
        aa = a.children[0]
        aaa = aa.children[0]
        ba = b.children[0]
        self.assertEqual(0, a.size)
        self.assertEqual(0, aa.size)
        self.assertEqual(0, aaa.size)
        self.assertEqual(512+7, b.size)
        self.assertEqual(512+7, ba.size)
        self.assertEqual(1234, c.size)

    def test_lftp_status_file_size(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        size = scanner._lftp_status_file_size("""
        size=243644865
        0.pos=31457280
        0.limit=60911217
        1.pos=87060081
        1.limit=121822433
        2.pos=144268513
        2.limit=182733649
        3.pos=207473489
        3.limit=243644865
        """)
        self.assertEqual(104792064, size)

    def test_lftp_status_file_size_accepts_base_only_checkpoint_conservatively(self):
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        for covered_size in (0, 5, 10):
            with self.subTest(covered_size=covered_size):
                self.assertEqual(
                    covered_size,
                    scanner._lftp_status_file_size(
                        "size=10\n0.pos={}\n".format(covered_size),
                    ),
                )

    def test_lftp_status_file_size_rejects_base_only_without_segment_zero(self):
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        self.assertIsNone(scanner._lftp_status_file_size("size=10\n1.pos=5\n"))

    def test_lftp_status_file_size_rejects_base_only_position_overrun(self):
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        self.assertIsNone(scanner._lftp_status_file_size("size=10\n0.pos=11\n"))

    def test_lftp_status_bytes_rejects_invalid_utf8(self):
        self.assertIsNone(
            parse_lftp_pget_status_bytes(b"size=10\n0.pos=9\n\xff"),
        )

    def test_lftp_status_file_size_rejects_oversize_content(self):
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        status = "size=1\n0.pos=0\n0.limit=1\n" + (" " * (64 * 1024))
        self.assertIsNone(scanner._lftp_status_file_size(status))

    def test_lftp_status_file_size_caps_paired_segments_at_256(self):
        scanner = SystemScanner(TestSystemScanner.temp_dir)

        def status_for(segment_count):
            lines = ["size={}".format(segment_count)]
            for index in range(segment_count):
                lines.extend(("{}.pos={}".format(index, index),
                              "{}.limit={}".format(index, index + 1)))
            return "\n".join(lines)

        self.assertEqual(0, scanner._lftp_status_file_size(status_for(256)))
        self.assertIsNone(scanner._lftp_status_file_size(status_for(257)))

    def test_lftp_status_file_size_rejects_malformed_status(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        size = scanner._lftp_status_file_size("""
        size=-2
        0.pos=0
        """)
        self.assertIsNone(size)

    def test_lftp_status_file_size_rejects_impossible_range(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        size = scanner._lftp_status_file_size("""
        size=1
        0.pos=2
        0.limit=3
        """)
        self.assertIsNone(size)

    def test_scan_lftp_partial_file(self):
        tempdir = TestSystemScanner.temp_dir

        # Create a partial file
        os.mkdir(os.path.join(tempdir, "t"))
        path = os.path.join(tempdir, "t", "partial.mkv")
        with open(path, 'wb') as f:
            f.write(bytearray([0xff] * 24588))
        # Write the lftp status out
        path = os.path.join(tempdir, "t", "partial.mkv.lftp-pget-status")
        with open(path, "w") as f:
            f.write("""
            size=24588
            0.pos=3157
            0.limit=6147
            1.pos=11578
            1.limit=12294
            2.pos=12295
            2.limit=18441
            3.pos=20000
            3.limit=24588
            """)
        scanner = SystemScanner(tempdir)
        files = scanner.scan()
        self.assertEqual(1, len(files))
        t = files[0]
        self.assertEqual("t", t.name)
        self.assertEqual(10148, t.size)
        self.assertEqual(1, len(t.children))
        partial_mkv = t.children[0]
        self.assertEqual("partial.mkv", partial_mkv.name)
        self.assertEqual(10148, partial_mkv.size)

    def test_scan_single_lftp_partial_file(self):
        # Scan a single partial file
        tempdir = TestSystemScanner.temp_dir

        # Create a partial file
        path = os.path.join(tempdir, "partial.mkv")
        with open(path, 'wb') as f:
            f.write(bytearray([0xff] * 24588))
        # Write the lftp status out
        path = os.path.join(tempdir, "partial.mkv.lftp-pget-status")
        with open(path, "w") as f:
            f.write("""
            size=24588
            0.pos=3157
            0.limit=6147
            1.pos=11578
            1.limit=12294
            2.pos=12295
            2.limit=18441
            3.pos=20000
            3.limit=24588
            """)
        scanner = SystemScanner(tempdir)
        partial_mkv = scanner.scan_single("partial.mkv")
        self.assertEqual("partial.mkv", partial_mkv.name)
        self.assertEqual(10148, partial_mkv.size)

    def test_scan_single_lftp_temp_file_with_malformed_status_is_conservative(self):
        tempdir = TestSystemScanner.temp_dir

        temp_path = os.path.join(tempdir, "partial.mkv.lftp")
        with open(temp_path, 'wb') as f:
            f.write(bytearray([0xff] * 24588))
        status_path = os.path.join(tempdir, "partial.mkv.lftp.lftp-pget-status")
        with open(status_path, "w") as f:
            f.write("""
            size=-2
            0.pos=0
            """)

        scanner = SystemScanner(tempdir)
        scanner.set_lftp_temp_suffix(".lftp")
        partial_mkv = scanner.scan_single("partial.mkv")
        self.assertEqual("partial.mkv", partial_mkv.name)
        self.assertEqual(0, partial_mkv.size)

    def test_scan_lftp_partial_file_with_oversize_status_is_conservative(self):
        tempdir = TestSystemScanner.temp_dir
        path = os.path.join(tempdir, "partial.mkv")
        with open(path, "wb") as handle:
            handle.write(b"partial")
        with open(path + ".lftp-pget-status", "w", encoding="utf-8") as handle:
            handle.write("size=7\n0.pos=0\n0.limit=7\n")
            handle.write(" " * (64 * 1024))

        partial_mkv = SystemScanner(tempdir).scan_single("partial.mkv")
        self.assertEqual("partial.mkv", partial_mkv.name)
        self.assertEqual(7, partial_mkv.size)
        self.assertFalse(partial_mkv.status_sidecar_ready)

    def test_scan_lftp_partial_file_rejects_crlf_padded_oversize_status(self):
        tempdir = TestSystemScanner.temp_dir
        path = os.path.join(tempdir, "partial.mkv")
        with open(path, "wb") as handle:
            handle.write(b"partial")
        status = "size=7\r\n0.pos=0\r\n0.limit=7\r\n" + ("\r\n" * 32760)
        with open(path + ".lftp-pget-status", "wb") as handle:
            handle.write(status.encode("utf-8"))

        partial_mkv = SystemScanner(tempdir).scan_single("partial.mkv")
        self.assertEqual(7, partial_mkv.size)
        self.assertFalse(partial_mkv.status_sidecar_ready)

    def test_scan_lftp_partial_file_accepts_under_limit_crlf_status(self):
        tempdir = TestSystemScanner.temp_dir
        path = os.path.join(tempdir, "partial.mkv")
        with open(path, "wb") as handle:
            handle.write(b"partial")
        with open(path + ".lftp-pget-status", "wb") as handle:
            handle.write(b"size=7\r\n0.pos=0\r\n0.limit=7\r\n")

        partial_mkv = SystemScanner(tempdir).scan_single("partial.mkv")
        self.assertEqual(0, partial_mkv.size)
        self.assertTrue(partial_mkv.status_sidecar_ready)

    def test_scan_lftp_temp_file(self):
        tempdir = TestSystemScanner.temp_dir

        # Create some temp and non-temp files
        temp1 = os.path.join(tempdir, "a.mkv.lftp")
        with open(temp1, 'wb') as f:
            f.write(bytearray([0xff] * 100))
        temp2 = os.path.join(tempdir, "b.rar.lftp")
        with open(temp2, 'wb') as f:
            f.write(bytearray([0xff] * 200))
        nontemp1 = os.path.join(tempdir, "c.rar")
        with open(nontemp1, 'wb') as f:
            f.write(bytearray([0xff] * 300))
        nontemp2 = os.path.join(tempdir, "d.lftp.avi")
        with open(nontemp2, 'wb') as f:
            f.write(bytearray([0xff] * 400))
        nontemp3 = os.path.join(tempdir, "e")
        os.mkdir(nontemp3)
        temp3 = os.path.join(nontemp3, "ea.txt.lftp")
        with open(temp3, 'wb') as f:
            f.write(bytearray([0xff] * 500))
        nontemp4 = os.path.join(tempdir, "f.lftp")
        os.mkdir(nontemp4)

        scanner = SystemScanner(tempdir)

        # No temp suffix set
        files = scanner.scan()
        self.assertEqual(6, len(files))
        a, b, c, d, e, f = tuple(files)
        self.assertEqual("a.mkv.lftp", a.name)
        self.assertEqual(100, a.size)
        self.assertEqual(False, a.is_dir)
        self.assertEqual("b.rar.lftp", b.name)
        self.assertEqual(200, b.size)
        self.assertEqual(False, b.is_dir)
        self.assertEqual("c.rar", c.name)
        self.assertEqual(300, c.size)
        self.assertEqual(False, c.is_dir)
        self.assertEqual("d.lftp.avi", d.name)
        self.assertEqual(400, d.size)
        self.assertEqual(False, d.is_dir)
        self.assertEqual("e", e.name)
        self.assertEqual(500, e.size)
        self.assertEqual(True, e.is_dir)
        self.assertEqual(1, len(e.children))
        ea = e.children[0]
        self.assertEqual("ea.txt.lftp", ea.name)
        self.assertEqual(500, ea.size)
        self.assertEqual(False, ea.is_dir)
        self.assertEqual("f.lftp", f.name)
        self.assertEqual(0, f.size)
        self.assertEqual(True, f.is_dir)

        # Temp suffix set
        path = os.path.join(tempdir, "a.mkv.lftp.lftp-pget-status")
        with open(path, "w") as f:
            f.write("""
            size=100
            0.pos=30
            0.limit=100
            """)
        scanner.set_lftp_temp_suffix(".lftp")
        files = scanner.scan()
        self.assertEqual(6, len(files))
        a, b, c, d, e, f = tuple(files)
        self.assertEqual("a.mkv", a.name)
        self.assertEqual(30, a.size)
        self.assertEqual(False, a.is_dir)
        self.assertEqual("b.rar", b.name)
        self.assertEqual(0, b.size)
        self.assertEqual(False, b.is_dir)
        self.assertEqual("c.rar", c.name)
        self.assertEqual(300, c.size)
        self.assertEqual(False, c.is_dir)
        self.assertEqual("d.lftp.avi", d.name)
        self.assertEqual(400, d.size)
        self.assertEqual(False, d.is_dir)
        self.assertEqual("e", e.name)
        self.assertEqual(0, e.size)
        self.assertEqual(True, e.is_dir)
        self.assertEqual(1, len(e.children))
        ea = e.children[0]
        self.assertEqual("ea.txt", ea.name)
        self.assertEqual(0, ea.size)
        self.assertEqual(False, ea.is_dir)
        self.assertEqual("f.lftp", f.name)
        self.assertEqual(0, f.size)
        self.assertEqual(True, f.is_dir)

    def test_scan_single_lftp_temp_file(self):
        tempdir = TestSystemScanner.temp_dir

        # Create:
        #   temp file
        #   non-temp file and
        #   non-temp directory with temp name
        #   non-temp directory with non-temp name
        temp1 = os.path.join(tempdir, "a.mkv.lftp")
        with open(temp1, 'wb') as f:
            f.write(bytearray([0xff] * 100))

        nontemp1 = os.path.join(tempdir, "b.rar")
        with open(nontemp1, 'wb') as f:
            f.write(bytearray([0xff] * 300))

        nontemp2 = os.path.join(tempdir, "c.lftp")
        os.mkdir(nontemp2)
        temp2 = os.path.join(nontemp2, "c.txt.lftp")
        with open(temp2, 'wb') as f:
            f.write(bytearray([0xff] * 500))

        nontemp3 = os.path.join(tempdir, "d")
        os.mkdir(nontemp3)
        temp3 = os.path.join(nontemp3, "d.avi.lftp")
        with open(temp3, 'wb') as f:
            f.write(bytearray([0xff] * 600))

        scanner = SystemScanner(tempdir)

        # No temp suffix set, must include temp suffix in name param
        file = scanner.scan_single("a.mkv.lftp")
        self.assertEqual("a.mkv.lftp", file.name)
        self.assertEqual(100, file.size)
        self.assertEqual(False, file.is_dir)

        file = scanner.scan_single("b.rar")
        self.assertEqual("b.rar", file.name)
        self.assertEqual(300, file.size)
        self.assertEqual(False, file.is_dir)

        file = scanner.scan_single("c.lftp")
        self.assertEqual("c.lftp", file.name)
        self.assertEqual(500, file.size)
        self.assertEqual(True, file.is_dir)
        self.assertEqual(1, len(file.children))
        child = file.children[0]
        self.assertEqual("c.txt.lftp", child.name)
        self.assertEqual(500, child.size)
        self.assertEqual(False, child.is_dir)

        file = scanner.scan_single("d")
        self.assertEqual("d", file.name)
        self.assertEqual(600, file.size)
        self.assertEqual(True, file.is_dir)
        child = file.children[0]
        self.assertEqual("d.avi.lftp", child.name)
        self.assertEqual(600, child.size)
        self.assertEqual(False, child.is_dir)

        # Temp suffix set, must NOT include temp suffix in name param
        path = os.path.join(tempdir, "a.mkv.lftp.lftp-pget-status")
        with open(path, "w") as f:
            f.write("""
            size=100
            0.pos=30
            0.limit=100
            """)
        scanner.set_lftp_temp_suffix(".lftp")
        file = scanner.scan_single("a.mkv")
        self.assertEqual("a.mkv", file.name)
        self.assertEqual(30, file.size)
        self.assertEqual(False, file.is_dir)

        file = scanner.scan_single("b.rar")
        self.assertEqual("b.rar", file.name)
        self.assertEqual(300, file.size)
        self.assertEqual(False, file.is_dir)

        file = scanner.scan_single("c.lftp")
        self.assertEqual("c.lftp", file.name)
        self.assertEqual(0, file.size)
        self.assertEqual(True, file.is_dir)
        self.assertEqual(1, len(file.children))
        child = file.children[0]
        self.assertEqual("c.txt", child.name)
        self.assertEqual(0, child.size)
        self.assertEqual(False, child.is_dir)
        # also, shouldn't look for directories with temp suffix
        with self.assertRaises(SystemScannerError) as ctx:
            scanner.scan_single("c")
        self.assertTrue("Path does not exist" in str(ctx.exception))

        file = scanner.scan_single("d")
        self.assertEqual("d", file.name)
        self.assertEqual(0, file.size)
        self.assertEqual(True, file.is_dir)
        child = file.children[0]
        self.assertEqual("d.avi", child.name)
        self.assertEqual(0, child.size)
        self.assertEqual(False, child.is_dir)

        # No file and no temp file
        with self.assertRaises(SystemScannerError) as ctx:
            scanner.scan_single("blah")
        self.assertTrue("Path does not exist" in str(ctx.exception))

    def test_files_deleted_while_scanning(self):
        self.setup_default_tree()
        scanner = SystemScanner(TestSystemScanner.temp_dir)

        stop = False

        # Make and delete files while test runs
        def monkey_with_files():
            orig = os.path.join(TestSystemScanner.temp_dir, "b")
            dest = os.path.join(TestSystemScanner.temp_dir, "b_copy")
            while not stop:
                shutil.copytree(orig, dest)
                shutil.rmtree(dest)
        thread = Thread(target=monkey_with_files)
        thread.start()

        try:
            # Scan a bunch of times
            for i in range(0, 2000):
                files = scanner.scan()
                # Must have at least the untouched files
                self.assertGreaterEqual(len(files), 3)
                names = set([f.name for f in files])
                self.assertIn("a", names)
                self.assertIn("b", names)
                self.assertIn("c", names)
        finally:
            stop = True
            thread.join()

    def test_scan_modified_time(self):
        self.setup_default_tree()
        # directory
        os.utime(
            os.path.join(TestSystemScanner.temp_dir, "a"),
            (
                datetime.now().timestamp(),
                datetime(2018, 11, 9, 21, 40, 18).timestamp()
            )
        )

        # file
        os.utime(
            os.path.join(TestSystemScanner.temp_dir, "c"),
            (
                datetime.now().timestamp(),
                datetime(2018, 11, 9, 21, 40, 17).timestamp()
            )
        )

        scanner = SystemScanner(TestSystemScanner.temp_dir)
        files = scanner.scan()
        self.assertEqual(3, len(files))
        a, b, c = tuple(files)

        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18), a.timestamp_modified)
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 17), c.timestamp_modified)

    def test_scan_created_time_falls_back_to_ctime_when_birthtime_missing(self):
        class FakeStat:
            st_ctime = datetime(2018, 11, 9, 21, 40, 17).timestamp()
            st_mtime = datetime(2018, 11, 9, 21, 40, 18).timestamp()
            st_size = 321

        class FakeEntry:
            name = "c"
            path = os.path.join(TestSystemScanner.temp_dir, "c")

            @staticmethod
            def is_dir():
                return False

            @staticmethod
            def stat():
                return FakeStat()

        scanner = SystemScanner(TestSystemScanner.temp_dir)
        file = scanner._SystemScanner__create_system_file(FakeEntry())

        self.assertEqual(datetime(2018, 11, 9, 21, 40, 17), file.timestamp_created)
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18), file.timestamp_modified)

    def test_scan_created_time_prefers_birthtime_when_available(self):
        class FakeStat:
            st_birthtime = datetime(2018, 11, 9, 21, 40, 17).timestamp()
            st_ctime = datetime(2018, 11, 9, 21, 40, 16).timestamp()
            st_mtime = datetime(2018, 11, 9, 21, 40, 18).timestamp()
            st_size = 321

        class FakeEntry:
            name = "c"
            path = os.path.join(TestSystemScanner.temp_dir, "c")

            @staticmethod
            def is_dir():
                return False

            @staticmethod
            def stat():
                return FakeStat()

        scanner = SystemScanner(TestSystemScanner.temp_dir)
        file = scanner._SystemScanner__create_system_file(FakeEntry())

        self.assertEqual(datetime(2018, 11, 9, 21, 40, 17), file.timestamp_created)

    def test_scan_file_with_unicode_chars(self):
        tempdir = TestSystemScanner.temp_dir
        # déģķ [dir]
        # dőÀ× [file, 128 bytes]
        my_mkdir("déģķ")
        my_touch(128, "dőÀ")

        scanner = SystemScanner(tempdir)
        files = scanner.scan()
        self.assertEqual(2, len(files))
        folder, file = tuple(files)
        self.assertEqual(0, len(folder.children))
        self.assertEqual("déģķ", folder.name)
        self.assertEqual("dőÀ", file.name)
        self.assertEqual(128, file.size)

    def test_lftp_sidecar_breadcrumb_is_gated_and_contains_no_runtime_identity(self):
        my_touch(4, "download.zip.lftp")
        with open(os.path.join(TestSystemScanner.temp_dir, "download.zip.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=100\n0.pos=30\n0.limit=100\n")

        trace = MagicMock()
        trace.is_effectively_enabled.return_value = False
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        scanner.set_lftp_temp_suffix(".lftp")
        scanner.set_scan_role("active")
        scanner.set_breadcrumb_trace(trace)
        with patch("system.scanner.opaque_trace_correlation", side_effect=AssertionError("gate missed")), \
                patch("system.scanner.lftp_sidecar_target_identity", side_effect=AssertionError("identity built")):
            result = scanner.scan_single("download.zip")

        self.assertEqual(30, result.size)
        trace.record.assert_not_called()

    def test_lftp_sidecar_breadcrumb_classifies_valid_malformed_and_absent(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        scanner.set_breadcrumb_trace(trace)
        scanner.set_scan_role("local")

        my_touch(4, "valid.lftp")
        with open(os.path.join(TestSystemScanner.temp_dir, "valid.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=100\n0.pos=30\n0.limit=100\n")
        my_touch(4, "malformed.lftp")
        with open(os.path.join(TestSystemScanner.temp_dir, "malformed.lftp.lftp-pget-status"), "w") as handle:
            handle.write("size=-2\n0.pos=0\n")
        my_touch(4, "absent.lftp")

        scanner.set_lftp_temp_suffix(".lftp")
        scanner.scan_single("valid")
        scanner.scan_single("malformed")
        scanner.scan_single("absent")

        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual({"valid", "malformed", "absent"}, {
            call.args[2]["classification"] for call in events
        })
        for event in events:
            details = event.args[2]
            self.assertEqual(False, details["status_only"])
            self.assertIn(details["parser_coverage"], {"known", "unknown"})
            self.assertEqual("local", details["scan_role"])
            self.assertNotIn(TestSystemScanner.temp_dir, repr(event))

    def test_lftp_sidecar_does_not_emit_absent_for_ordinary_files(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        scanner.set_breadcrumb_trace(trace)
        for name in ("ordinary-a.txt", "ordinary-b.bin", "ordinary-c.mkv"):
            my_touch(4, name)

        scanner.scan()

        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual([], events)

    def test_lftp_sidecar_emits_absent_for_temp_target_without_sidecar(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        scanner = SystemScanner(TestSystemScanner.temp_dir)
        scanner.set_lftp_temp_suffix(".lftp")
        scanner.set_breadcrumb_trace(trace)
        my_touch(4, "missing-status.lftp")

        scanner.scan()

        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(1, len(events))
        self.assertEqual("absent", events[0].args[2]["classification"])

    def test_lftp_sidecar_state_transitions_keep_target_corr_id_but_split_coalescing(self):
        trace = MagicMock()
        trace.is_effectively_enabled.return_value = True
        target = lftp_sidecar_target_identity(TestSystemScanner.temp_dir, "download.zip")

        _record_lftp_sidecar_breadcrumb(
            trace, "valid", target, status_only=False, parser_coverage="known", scan_role="local",
        )
        _record_lftp_sidecar_breadcrumb(
            trace, "malformed", target, status_only=False, parser_coverage="known", scan_role="local",
        )

        events = [call for call in trace.record.call_args_list if call.args[1] == "lftp_sidecar_classified"]
        self.assertEqual(2, len(events))
        self.assertEqual(events[0].kwargs["corr_id"], events[1].kwargs["corr_id"])
        self.assertNotEqual(events[0].kwargs["_coalesce_key"], events[1].kwargs["_coalesce_key"])

    def test_lftp_sidecar_target_identity_distinguishes_roots_with_same_basename(self):
        first = lftp_sidecar_target_identity(os.path.join(TestSystemScanner.temp_dir, "movies"), "same.zip")
        second = lftp_sidecar_target_identity(os.path.join(TestSystemScanner.temp_dir, "tv"), "same.zip")
        self.assertNotEqual(first, second)
        self.assertNotIn(TestSystemScanner.temp_dir, first)
        self.assertNotIn(TestSystemScanner.temp_dir, second)

    def test_scan_file_with_latin_chars(self):
        tempdir = TestSystemScanner.temp_dir
        # a\xe9b [dir]
        # c\xe9d [file, 128 bytes]
        my_mkdir_latin(b"dir\xe9dir")
        my_touch_latin(128, b"file\xd9file")

        scanner = SystemScanner(tempdir)
        files = scanner.scan()
        self.assertEqual(2, len(files))
        folder, file = tuple(files)
        self.assertEqual(0, len(folder.children))
        self.assertEqual("dir�dir", folder.name)
        self.assertEqual("file�file", file.name)
        self.assertEqual(128, file.size)

    @unittest.skipIf(os.name == "nt", "Directory symlinks require elevated Windows privileges")
    def test_scan_skips_directory_symlink_outside_root(self):
        outside = tempfile.mkdtemp(prefix="test_scanner_outside_")
        link_path = os.path.join(TestSystemScanner.temp_dir, "link_to_outside")
        try:
            with open(os.path.join(outside, "secret.txt"), "wb") as f:
                f.write(b"x" * 100)
            os.symlink(outside, link_path)

            files = SystemScanner(TestSystemScanner.temp_dir).scan()
            self.assertEqual([], files)
        finally:
            if os.path.lexists(link_path):
                os.unlink(link_path)
            shutil.rmtree(outside)

    @unittest.skipIf(os.name == "nt", "Directory symlink cycles require elevated Windows privileges")
    @pytest.mark.timeout(15)
    def test_scan_skips_cyclic_directory_symlink(self):
        loop_path = os.path.join(TestSystemScanner.temp_dir, "loop")
        os.symlink(TestSystemScanner.temp_dir, loop_path)
        try:
            files = SystemScanner(TestSystemScanner.temp_dir).scan()
            self.assertEqual([], files)
        finally:
            if os.path.lexists(loop_path):
                os.unlink(loop_path)
