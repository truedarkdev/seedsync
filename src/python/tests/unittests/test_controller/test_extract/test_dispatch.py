# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import os
from unittest.mock import patch, MagicMock, call
import time
import threading
import logging
import sys

import pytest

from common import overrides
from model import ModelFile
from controller.extract import ExtractDispatch, ExtractDispatchError, ExtractListener, \
                                ExtractError, ExtractStatus


pytestmark = pytest.mark.timeout(2)

class DummyExtractListener(ExtractListener):
    @overrides(ExtractListener)
    def extract_completed(self, name: str, is_dir: bool, file_id: str = None, path_pair_id: str = None):
        pass

    @overrides(ExtractListener)
    def extract_failed(self, name: str, is_dir: bool, file_id: str = None, path_pair_id: str = None):
        pass


class TestExtractDispatch(unittest.TestCase):
    def setUp(self):
        extract_patcher = patch('controller.extract.dispatch.Extract')
        self.addCleanup(extract_patcher.stop)
        mock_extract_module = extract_patcher.start()
        self.mock_is_archive = mock_extract_module.is_archive
        self.mock_extract_archive = mock_extract_module.extract_archive

        marker_patcher = patch('controller.extract.dispatch.write_managed_extract_marker')
        self.addCleanup(marker_patcher.stop)
        self.mock_write_managed_extract_marker = marker_patcher.start()

        self.out_dir_path = os.path.join("out", "dir")
        self.local_path = os.path.join("local", "path")
        self.dispatch = ExtractDispatch(
            out_dir_path=self.out_dir_path,
            local_path=self.local_path
        )

        self.listener = DummyExtractListener()
        self.listener.extract_completed = MagicMock()
        self.listener.extract_failed = MagicMock()

        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        self.dispatch.start()

    def tearDown(self):
        if self.dispatch:
            self.dispatch.stop()

    def _wait_for_call_count(self, mock, expected_count: int, timeout_seconds: float = 1.0):
        deadline = time.time() + timeout_seconds
        while mock.call_count < expected_count:
            if time.time() >= deadline:
                self.fail(
                    "Timed out waiting for {} calls, saw {}".format(expected_count, mock.call_count)
                )
            time.sleep(0.01)

    def test_extract_single_raises_error_on_remote_only_file(self):
        mf = ModelFile("aaa", False)
        mf.local_size = None
        with self.assertRaises(ExtractDispatchError) as ctx:
            self.dispatch.extract(mf)
        self.assertTrue(str(ctx.exception).startswith("File does not exist locally"))

        mf = ModelFile("aaa", False)
        mf.local_size = 0
        with self.assertRaises(ExtractDispatchError) as ctx:
            self.dispatch.extract(mf)
        self.assertTrue(str(ctx.exception).startswith("File does not exist locally"))

    def test_extract_single_raises_error_on_bad_archive(self):
        self.mock_is_archive.return_value = False

        mf = ModelFile("aaa", False)
        mf.local_size = 100

        with self.assertRaises(ExtractDispatchError) as ctx:
            self.dispatch.extract(mf)
        self.assertTrue(str(ctx.exception).startswith("File is not an archive"))
        self.mock_is_archive.assert_called_once_with(os.path.join(self.local_path, mf.name))

    def test_extract_single_uses_fallback_path_when_primary_missing(self):
        fallback_path = os.path.join("fallback", "path")
        dispatch = ExtractDispatch(
            out_dir_path=self.out_dir_path,
            local_path=self.local_path,
            local_path_fallback=fallback_path
        )
        self.addCleanup(dispatch.stop)
        dispatch.start()

        def _is_archive(archive_path: str):
            return archive_path == os.path.join(fallback_path, "aaa")

        self.mock_is_archive.side_effect = _is_archive

        mf = ModelFile("aaa", False)
        mf.local_size = 100

        dispatch.extract(mf)

        self._wait_for_call_count(self.mock_extract_archive, 1)
        self.mock_is_archive.assert_has_calls([
            call(os.path.join(self.local_path, mf.name)),
            call(os.path.join(fallback_path, mf.name)),
        ])
        self.mock_extract_archive.assert_called_once_with(
            archive_path=os.path.join(fallback_path, "aaa"),
            out_dir_path=os.path.join(self.out_dir_path, "aaa")
        )

    def test_extract_single(self):
        self.mock_is_archive.return_value = True

        mf = ModelFile("aaa", False)
        mf.local_size = 100

        self.dispatch.extract(mf)

        while self.mock_extract_archive.call_count < 1:
            pass
        self.mock_extract_archive.assert_called_once_with(
            archive_path=os.path.join(self.local_path, "aaa"),
            out_dir_path=os.path.join(self.out_dir_path, "aaa")
        )
        self.mock_write_managed_extract_marker.assert_called_once_with(
            os.path.join(self.out_dir_path, "aaa"),
            archive_name="aaa",
            archive_file_id="aaa",
            path_pair_id=None
        )

    def test_extract_dir_does_not_duplicate_same_named_managed_folder(self):
        self.mock_is_archive.return_value = True

        root = ModelFile("rd", True)
        root.local_size = 100
        archive = ModelFile("rd.zip", False)
        archive.local_size = 100
        root.add_child(archive)

        self.dispatch.stop()
        self.dispatch = ExtractDispatch(
            out_dir_path=self.out_dir_path,
            local_path=self.local_path
        )
        self.dispatch.start()
        self.dispatch.extract(root)

        self._wait_for_call_count(self.mock_extract_archive, 1)
        self.mock_extract_archive.assert_called_once_with(
            archive_path=os.path.join(self.local_path, "rd", "rd.zip"),
            out_dir_path=os.path.join(self.out_dir_path, "rd")
        )
        self.mock_write_managed_extract_marker.assert_called_once_with(
            os.path.join(self.out_dir_path, "rd"),
            archive_name="rd.zip",
            archive_file_id=os.path.join("rd", "rd.zip"),
            path_pair_id=None
        )

    def test_extract_dir_does_not_duplicate_same_named_managed_folder_mixed_case(self):
        self.mock_is_archive.return_value = True

        root = ModelFile("RD", True)
        root.local_size = 100
        archive = ModelFile("rd.zip", False)
        archive.local_size = 100
        root.add_child(archive)

        self.dispatch.extract(root)

        self._wait_for_call_count(self.mock_extract_archive, 1)
        self.mock_extract_archive.assert_called_once_with(
            archive_path=os.path.join(self.local_path, "RD", "rd.zip"),
            out_dir_path=os.path.join(self.out_dir_path, "RD")
        )
        self.mock_write_managed_extract_marker.assert_called_once_with(
            os.path.join(self.out_dir_path, "RD"),
            archive_name="rd.zip",
            archive_file_id=os.path.join("RD", "rd.zip"),
            path_pair_id=None
        )

    def test_extract_maintains_order(self):
        self.mock_is_archive.return_value = True

        mf1 = ModelFile("aaa", False)
        mf1.local_size = 100
        mf2 = ModelFile("bbb", False)
        mf2.local_size = 100
        mf3 = ModelFile("ccc", False)
        mf3.local_size = 100

        self.dispatch.extract(mf1)
        self.dispatch.extract(mf2)
        self.dispatch.extract(mf3)

        while self.mock_extract_archive.call_count < 3:
            pass
        self.assertEqual(3, self.mock_extract_archive.call_count)
        args_list = self.mock_extract_archive.call_args_list
        self.assertEqual(args_list, [
            call(
                archive_path=os.path.join(self.local_path, "aaa"),
                out_dir_path=os.path.join(self.out_dir_path, "aaa")
            ),
            call(
                archive_path=os.path.join(self.local_path, "bbb"),
                out_dir_path=os.path.join(self.out_dir_path, "bbb")
            ),
            call(
                archive_path=os.path.join(self.local_path, "ccc"),
                out_dir_path=os.path.join(self.out_dir_path, "ccc")
            )
        ])

    def test_extract_calls_listener_on_completed(self):
        self.mock_is_archive.return_value = True

        mf1 = ModelFile("aaa", False)
        mf1.local_size = 100

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(mf1)

        while self.mock_extract_archive.call_count < 1 \
                or self.listener.extract_completed.call_count < 1:
            pass
        self.assertEqual(1, self.mock_extract_archive.call_count)
        self.listener.extract_completed.assert_called_once_with("aaa", False, "aaa", None)
        self.listener.extract_failed.assert_not_called()

    def test_extract_calls_listener_on_failed(self):
        self.mock_is_archive.return_value = True

        # noinspection PyUnusedLocal
        def _extract_archive(**kwargs):
            raise ExtractError()
        self.mock_extract_archive.side_effect = _extract_archive

        mf1 = ModelFile("aaa", False)
        mf1.local_size = 100

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(mf1)

        while self.mock_extract_archive.call_count < 1 \
                or self.listener.extract_failed.call_count < 1:
            pass
        self.assertEqual(1, self.mock_extract_archive.call_count)
        self.listener.extract_completed.assert_not_called()
        self.listener.extract_failed.assert_called_once_with("aaa", False, "aaa", None)

    @pytest.mark.timeout(5)
    def test_extract_calls_listeners_in_correct_sequence(self):
        self.mock_is_archive.return_value = True
        self.count = 0

        # noinspection PyUnusedLocal
        def _extract_archive(**kwargs):
            # raise error for first and third extractions
            self.count += 1
            if self.count in (1, 3):
                raise ExtractError()
        self.mock_extract_archive.side_effect = _extract_archive

        mf1 = ModelFile("aaa", False)
        mf1.local_size = 100
        mf2 = ModelFile("bbb", False)
        mf2.local_size = 100
        mf3 = ModelFile("ccc", False)
        mf3.local_size = 100

        listener_calls = []

        def _completed(name, is_dir, file_id=None, path_pair_id=None):
            listener_calls.append((True, name, is_dir))

        def _failed(name, is_dir, file_id=None, path_pair_id=None):
            listener_calls.append((False, name, is_dir))

        self.listener.extract_completed.side_effect = _completed
        self.listener.extract_failed.side_effect = _failed

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(mf1)
        self.dispatch.extract(mf2)
        self.dispatch.extract(mf3)

        while self.mock_extract_archive.call_count < 3 \
                or self.listener.extract_failed.call_count < 2 \
                or self.listener.extract_completed.call_count < 1:
            pass
        self.assertEqual(3, self.mock_extract_archive.call_count)
        self.assertEqual(1, self.mock_write_managed_extract_marker.call_count)
        self.assertEqual(
            [(False, "aaa", False), (True, "bbb", False), (False, "ccc", False)],
            listener_calls
        )

    def test_extract_skips_remaining_on_shutdown(self):
        # Send two extract commands
        # Call shutdown after first one runs
        # Check that second command did not run
        self.mock_is_archive.return_value = True

        self.call_stop = False

        def _extract_archive(**kwargs):
            print(kwargs)
            self.call_stop = True
            time.sleep(0.5)  # wait a bit so shutdown is called

        self.mock_extract_archive.side_effect = _extract_archive

        mf1 = ModelFile("aaa", False)
        mf1.local_size = 100
        mf2 = ModelFile("bbb", False)
        mf2.local_size = 100

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(mf1)
        self.dispatch.extract(mf2)

        while not self.call_stop:
            pass
        self.dispatch.stop()

        while self.mock_extract_archive.call_count < 1 \
                or self.listener.extract_completed.call_count < 1:
            pass
        self.assertEqual(1, self.mock_extract_archive.call_count)
        self.listener.extract_completed.assert_called_once_with("aaa", False, "aaa", None)
        self.listener.extract_failed.assert_not_called()

    def test_extract_dir_raises_error_on_empty_dir(self):
        mf = ModelFile("aaa", True)

        with self.assertRaises(ExtractDispatchError) as ctx:
            self.dispatch.extract(mf)
        self.assertTrue(str(ctx.exception).startswith("Directory does not contain any archives"))

    def test_extract_dir_raises_error_on_no_archives(self):
        self.mock_is_archive.return_value = False

        a = ModelFile("a", True)
        a.local_size = 100
        aa = ModelFile("aa", False)
        aa.local_size = 50
        a.add_child(aa)
        ab = ModelFile("ab", False)
        ab.local_size = 50
        a.add_child(ab)

        with self.assertRaises(ExtractDispatchError) as ctx:
            self.dispatch.extract(a)
        self.assertTrue(str(ctx.exception).startswith("Directory does not contain any archives"))

    def test_extract_dir_raises_error_on_no_local_files(self):
        self.mock_is_archive.return_value = True

        a = ModelFile("a", True)
        a.remote_size = 100
        aa = ModelFile("aa", False)
        aa.remote_size = 50
        a.add_child(aa)
        ab = ModelFile("ab", False)
        ab.remote_size = 50
        a.add_child(ab)

        with self.assertRaises(ExtractDispatchError) as ctx:
            self.dispatch.extract(a)
        self.assertTrue(str(ctx.exception).startswith("Directory does not contain any archives"))

    # noinspection SpellCheckingInspection
    def test_extract_dir(self):
        self.mock_is_archive.return_value = True
        self.actual_calls = set()

        def _extract(archive_path: str, out_dir_path: str):
            self.actual_calls.add((archive_path, out_dir_path))
        self.mock_extract_archive.side_effect = _extract

        a = ModelFile("a", True)
        a.local_size = 500
        aa = ModelFile("aa", True)
        aa.local_size = 300
        a.add_child(aa)
        aaa = ModelFile("aaa", False)
        aaa.local_size = 100
        aa.add_child(aaa)
        aab = ModelFile("aab", False)
        aab.local_size = 100
        aa.add_child(aab)
        aac = ModelFile("aac", True)
        aac.local_size = 100
        aa.add_child(aac)
        aaca = ModelFile("aaca", False)
        aaca.local_size = 100
        aac.add_child(aaca)
        ab = ModelFile("ab", True)
        ab.local_size = 100
        a.add_child(ab)
        aba = ModelFile("aba", False)
        aba.local_size = 100
        ab.add_child(aba)
        ac = ModelFile("ac", False)
        ac.local_size = 100
        a.add_child(ac)

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)
        while self.listener.extract_completed.call_count < 1:
            pass
        self.listener.extract_completed.assert_called_once_with("a", True, "a", None)

        golden_calls = {
            (
                os.path.join(self.local_path, "a", "aa", "aaa"),
                os.path.join(self.out_dir_path, "a", "aa", "aaa")
            ),
            (
                os.path.join(self.local_path, "a", "aa", "aab"),
                os.path.join(self.out_dir_path, "a", "aa", "aab")
            ),
            (
                os.path.join(self.local_path, "a", "aa", "aac", "aaca"),
                os.path.join(self.out_dir_path, "a", "aa", "aac", "aaca")
            ),
            (
                os.path.join(self.local_path, "a", "ab", "aba"),
                os.path.join(self.out_dir_path, "a", "ab", "aba")
            ),
            (
                os.path.join(self.local_path, "a", "ac"),
                os.path.join(self.out_dir_path, "a", "ac")
            ),
        }
        self.assertEqual(5, self.mock_extract_archive.call_count)
        self.assertEqual(5, self.mock_write_managed_extract_marker.call_count)
        self.assertEqual(golden_calls, self.actual_calls)

    # noinspection SpellCheckingInspection
    def test_extract_dir_skips_remote_files(self):
        self.mock_is_archive.return_value = True
        self.actual_calls = set()

        def _extract(archive_path: str, out_dir_path: str):
            self.actual_calls.add((archive_path, out_dir_path))
        self.mock_extract_archive.side_effect = _extract

        a = ModelFile("a", True)
        a.local_size = 500
        aa = ModelFile("aa", True)
        aa.local_size = 300
        a.add_child(aa)
        aaa = ModelFile("aaa", False)
        aaa.local_size = 100
        aa.add_child(aaa)
        aab = ModelFile("aab", False)
        aab.remote_size = 100
        aa.add_child(aab)
        aac = ModelFile("aac", True)
        aac.local_size = 100
        aa.add_child(aac)
        aaca = ModelFile("aaca", False)
        aaca.local_size = 100
        aac.add_child(aaca)
        ab = ModelFile("ab", True)
        ab.local_size = 100
        a.add_child(ab)
        aba = ModelFile("aba", False)
        aba.local_size = 100
        ab.add_child(aba)
        ac = ModelFile("ac", False)
        ac.remote_size = 100
        a.add_child(ac)

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)
        while self.listener.extract_completed.call_count < 1:
            pass
        self.listener.extract_completed.assert_called_once_with("a", True, "a", None)

        golden_calls = {
            (
                os.path.join(self.local_path, "a", "aa", "aaa"),
                os.path.join(self.out_dir_path, "a", "aa", "aaa")
            ),
            (
                os.path.join(self.local_path, "a", "aa", "aac", "aaca"),
                os.path.join(self.out_dir_path, "a", "aa", "aac", "aaca")
            ),
            (
                os.path.join(self.local_path, "a", "ab", "aba"),
                os.path.join(self.out_dir_path, "a", "ab", "aba")
            ),
        }
        self.assertEqual(3, self.mock_extract_archive.call_count)
        self.assertEqual(3, self.mock_write_managed_extract_marker.call_count)
        self.assertEqual(golden_calls, self.actual_calls)

    # noinspection SpellCheckingInspection
    def test_extract_dir_skips_non_archive_files(self):
        # noinspection SpellCheckingInspection
        def _is_archive(archive_path: str):
            return archive_path in (
                os.path.join(self.local_path, "a", "aa", "aaa"),
                os.path.join(self.local_path, "a", "aa", "aac", "aaca"),
                os.path.join(self.local_path, "a", "ab", "aba")
            )
        self.mock_is_archive.side_effect = _is_archive
        self.actual_calls = set()

        def _extract(archive_path: str, out_dir_path: str):
            self.actual_calls.add((archive_path, out_dir_path))
        self.mock_extract_archive.side_effect = _extract

        a = ModelFile("a", True)
        a.local_size = 500
        aa = ModelFile("aa", True)
        aa.local_size = 300
        a.add_child(aa)
        aaa = ModelFile("aaa", False)
        aaa.local_size = 100
        aa.add_child(aaa)
        aab = ModelFile("aab", False)
        aab.local_size = 100
        aa.add_child(aab)
        aac = ModelFile("aac", True)
        aac.local_size = 100
        aa.add_child(aac)
        aaca = ModelFile("aaca", False)
        aaca.local_size = 100
        aac.add_child(aaca)
        ab = ModelFile("ab", True)
        ab.local_size = 100
        a.add_child(ab)
        aba = ModelFile("aba", False)
        aba.local_size = 100
        ab.add_child(aba)
        ac = ModelFile("ac", False)
        ac.local_size = 100
        a.add_child(ac)

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)
        while self.listener.extract_completed.call_count < 1:
            pass
        self.listener.extract_completed.assert_called_once_with("a", True, "a", None)

        golden_calls = {
            (
                os.path.join(self.local_path, "a", "aa", "aaa"),
                os.path.join(self.out_dir_path, "a", "aa", "aaa")
            ),
            (
                os.path.join(self.local_path, "a", "aa", "aac", "aaca"),
                os.path.join(self.out_dir_path, "a", "aa", "aac", "aaca")
            ),
            (
                os.path.join(self.local_path, "a", "ab", "aba"),
                os.path.join(self.out_dir_path, "a", "ab", "aba")
            ),
        }
        self.assertEqual(3, self.mock_extract_archive.call_count)
        self.assertEqual(3, self.mock_write_managed_extract_marker.call_count)
        self.assertEqual(golden_calls, self.actual_calls)

    # noinspection SpellCheckingInspection
    def test_extract_dir_does_not_extract_split_rar_files(self):
        self.mock_is_archive.return_value = True
        self.actual_calls = set()

        def _extract(archive_path: str, out_dir_path: str):
            self.actual_calls.add((archive_path, out_dir_path))
        self.mock_extract_archive.side_effect = _extract

        a = ModelFile("a", True)
        a.local_size = 80
        aa = ModelFile("aa.rar", False)
        aa.local_size = 10
        a.add_child(aa)
        aa0 = ModelFile("aa.r00", False)
        aa0.local_size = 10
        a.add_child(aa0)
        aa1 = ModelFile("aa.r01", False)
        aa1.local_size = 10
        a.add_child(aa1)
        aa2 = ModelFile("aa.r02", False)
        aa2.local_size = 10
        a.add_child(aa2)
        aa15 = ModelFile("aa.r15", False)
        aa15.local_size = 10
        a.add_child(aa15)
        ab = ModelFile("ab.rar", False)
        ab.local_size = 10
        a.add_child(ab)
        ab0 = ModelFile("ab.r000", False)
        ab0.local_size = 10
        a.add_child(ab0)
        ab1 = ModelFile("ab.r001", False)
        ab1.local_size = 10
        a.add_child(ab1)
        ac = ModelFile("ac", True)
        ac.local_size = 20
        a.add_child(ac)
        aca = ModelFile("aca", True)
        aca.local_size = 20
        ac.add_child(aca)
        acaa = ModelFile("acaa.rar", False)
        acaa.local_size = 10
        aca.add_child(acaa)
        acaa0 = ModelFile("acaa.r00", False)
        acaa0.local_size = 10
        aca.add_child(acaa0)

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)
        while self.listener.extract_completed.call_count < 1:
            pass
        self.listener.extract_completed.assert_called_once_with("a", True, "a", None)

        golden_calls = {
            (
                os.path.join(self.local_path, "a", "aa.rar"),
                os.path.join(self.out_dir_path, "a", "aa")
            ),
            (
                os.path.join(self.local_path, "a", "ab.rar"),
                os.path.join(self.out_dir_path, "a", "ab")
            ),
            (
                os.path.join(self.local_path, "a", "ac", "aca", "acaa.rar"),
                os.path.join(self.out_dir_path, "a", "ac", "aca", "acaa")
            ),
        }
        self.assertEqual(3, self.mock_extract_archive.call_count)
        self.assertEqual(golden_calls, self.actual_calls)

    def test_extract_dir_exits_command_early_on_shutdown(self):
        # Send extract dir command with two archives
        # Call shutdown after first extract but before second
        # Verify second extract is not called
        self.mock_is_archive.return_value = True

        self.call_stop = False

        def _extract_archive(**kwargs):
            print(kwargs)
            self.call_stop = True
            time.sleep(0.5)  # wait a bit so shutdown is called

        self.mock_extract_archive.side_effect = _extract_archive

        a = ModelFile("a", True)
        a.local_size = 200
        aa = ModelFile("aa", False)
        aa.local_size = 100
        a.add_child(aa)
        ab = ModelFile("ab", False)
        ab.local_size = 100
        a.add_child(ab)

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)

        while not self.call_stop:
            pass
        self.dispatch.stop()

        while self.mock_extract_archive.call_count < 1 \
                or self.listener.extract_failed.call_count < 1:
            pass
        self.listener.extract_completed.assert_not_called()
        self.listener.extract_failed.assert_called_once_with("a", True, "a", None)
        self.assertEqual(1, self.mock_extract_archive.call_count)

    def test_status(self):
        self.mock_is_archive.return_value = True
        self.send_count = 0
        self.rx_count = 0

        # noinspection PyUnusedLocal
        def _extract(**kwargs):
            # barrier implementation
            while self.send_count <= self.rx_count:
                pass
            self.rx_count += 1
        self.mock_extract_archive.side_effect = _extract

        a = ModelFile("a", True)
        a.local_size = 200
        aa = ModelFile("aa", False)
        aa.local_size = 100
        a.add_child(aa)
        ab = ModelFile("ab", False)
        ab.local_size = 100
        a.add_child(ab)
        b = ModelFile("b", True)
        b.path_pair_id = "pair-b"
        b.local_size = 100
        ba = ModelFile("ba", False)
        ba.local_size = 100
        b.add_child(ba)
        c = ModelFile("c", False)
        c.local_size = 100

        # Initial status should be empty
        status = self.dispatch.status()
        self.assertEqual(0, len(status))

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)
        self.dispatch.extract(b)
        self.dispatch.extract(c)

        status = self.dispatch.status()
        self.assertEqual(3, len(status))
        self.assertEqual("a", status[0].name)
        self.assertEqual(True, status[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[0].state)
        self.assertEqual("a", status[0].file_id)
        self.assertEqual(None, status[0].path_pair_id)
        self.assertEqual("b", status[1].name)
        self.assertEqual(True, status[1].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[1].state)
        self.assertEqual(b.file_id, status[1].file_id)
        self.assertEqual("pair-b", status[1].path_pair_id)
        self.assertEqual("c", status[2].name)
        self.assertEqual(False, status[2].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[2].state)

        # Wait for first dir to start extracting
        self.send_count = 1
        while self.rx_count < self.send_count:
            pass

        status = self.dispatch.status()
        self.assertEqual(3, len(status))
        self.assertEqual("a", status[0].name)
        self.assertEqual(True, status[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[0].state)
        self.assertEqual("b", status[1].name)
        self.assertEqual(True, status[1].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[1].state)
        self.assertEqual("c", status[2].name)
        self.assertEqual(False, status[2].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[2].state)

        # After first directory finishes
        self.send_count = 2
        while self.listener.extract_completed.call_count < 1:
            pass
        self.listener.extract_completed.assert_called_with("a", True, "a", None)

        status = self.dispatch.status()
        self.assertEqual(2, len(status))
        self.assertEqual("b", status[0].name)
        self.assertEqual(True, status[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[0].state)
        self.assertEqual(b.file_id, status[0].file_id)
        self.assertEqual("pair-b", status[0].path_pair_id)
        self.assertEqual("c", status[1].name)
        self.assertEqual(False, status[1].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[1].state)

        # After second directory finishes
        self.send_count = 3
        while self.listener.extract_completed.call_count < 2:
            pass
        self.listener.extract_completed.assert_called_with("b", True, b.file_id, "pair-b")

        status = self.dispatch.status()
        self.assertEqual(1, len(status))
        self.assertEqual("c", status[0].name)
        self.assertEqual(False, status[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status[0].state)

        # After third/last file finishes
        self.send_count = 4
        while self.listener.extract_completed.call_count < 3:
            pass
        self.listener.extract_completed.assert_called_with("c", False, "c", None)

        status = self.dispatch.status()
        self.assertEqual(0, len(status))

    def test_extract_ignores_duplicate_calls(self):
        # Send two extract commands to same file
        # Expect that only one extract operation is performed
        self.mock_is_archive.return_value = True

        self.barrier = False

        def _extract_archive(**kwargs):
            print(kwargs)
            while not self.barrier:
                pass

        self.mock_extract_archive.side_effect = _extract_archive

        a = ModelFile("a", False)
        a.local_size = 200

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(a)
        self.dispatch.extract(a)

        time.sleep(0.1)
        self.barrier = True

        time.sleep(0.1)

        while self.mock_extract_archive.call_count < 1 or \
                self.listener.extract_completed.call_count < 1:
            pass
        time.sleep(0.1)
        self.listener.extract_completed.assert_called_once_with("a", False, "a", None)
        self.listener.extract_failed.assert_not_called()
        self.assertEqual(1, self.mock_extract_archive.call_count)


class TestExtractDispatchThreadSafety(unittest.TestCase):
    def setUp(self):
        extract_patcher = patch('controller.extract.dispatch.Extract')
        self.addCleanup(extract_patcher.stop)
        mock_extract_module = extract_patcher.start()
        self.mock_is_archive = mock_extract_module.is_archive
        self.mock_extract_archive = mock_extract_module.extract_archive

        marker_patcher = patch('controller.extract.dispatch.write_managed_extract_marker')
        self.addCleanup(marker_patcher.stop)
        self.mock_write_managed_extract_marker = marker_patcher.start()

        self.out_dir_path = os.path.join("out", "dir")
        self.local_path = os.path.join("local", "path")
        self.dispatch = ExtractDispatch(
            out_dir_path=self.out_dir_path,
            local_path=self.local_path
        )

        self.listener = DummyExtractListener()
        self.listener.extract_completed = MagicMock()
        self.listener.extract_failed = MagicMock()

        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)

        self.dispatch.start()

    def tearDown(self):
        if self.dispatch:
            self.dispatch.stop()

    @pytest.mark.timeout(5)
    def test_status_returns_consistent_snapshot(self):
        self.mock_is_archive.return_value = True

        files = []
        for i in range(10):
            model_file = ModelFile("file{}".format(i), False)
            model_file.local_size = 100
            files.append(model_file)

        barrier = threading.Event()

        def _block_extract(**kwargs):
            barrier.wait(timeout=5)

        self.mock_extract_archive.side_effect = _block_extract

        for model_file in files:
            self.dispatch.extract(model_file)

        for _ in range(20):
            status = self.dispatch.status()
            self.assertIsInstance(status, list)
            for extract_status in status:
                self.assertIsInstance(extract_status, ExtractStatus)

        barrier.set()

    @pytest.mark.timeout(5)
    def test_extract_duplicate_check_is_safe(self):
        self.mock_is_archive.return_value = True

        barrier = threading.Event()

        def _block_extract(**kwargs):
            barrier.wait(timeout=5)

        self.mock_extract_archive.side_effect = _block_extract

        model_file = ModelFile("aaa", False)
        model_file.local_size = 100

        self.dispatch.extract(model_file)

        errors = []

        def _try_extract():
            try:
                self.dispatch.extract(model_file)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=_try_extract) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([], errors)

        barrier.set()

    @pytest.mark.timeout(5)
    def test_listener_notification_allows_concurrent_add(self):
        self.mock_is_archive.return_value = True

        second_listener = DummyExtractListener()
        second_listener.extract_completed = MagicMock()
        second_listener.extract_failed = MagicMock()

        def _on_complete(name, is_dir, file_id=None, path_pair_id=None):
            self.dispatch.add_listener(second_listener)

        self.listener.extract_completed = MagicMock(side_effect=_on_complete)

        model_file = ModelFile("aaa", False)
        model_file.local_size = 100

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(model_file)

        while self.listener.extract_completed.call_count < 1:
            pass
        time.sleep(0.1)

        self.listener.extract_completed.assert_called_once_with("aaa", False, "aaa", None)
        second_listener.extract_completed.assert_not_called()

    @pytest.mark.timeout(5)
    def test_worker_survives_empty_queue_race_in_finally(self):
        self.mock_is_archive.return_value = True

        processed_archives = []

        def _extract_archive(**kwargs):
            processed_archives.append(kwargs["archive_path"])
            if len(processed_archives) == 1:
                with self.dispatch._ExtractDispatch__task_queue.mutex:
                    self.dispatch._ExtractDispatch__task_queue.queue.clear()

        self.mock_extract_archive.side_effect = _extract_archive

        first_file = ModelFile("aaa", False)
        first_file.local_size = 100
        second_file = ModelFile("bbb", False)
        second_file.local_size = 100

        self.dispatch.add_listener(self.listener)
        self.dispatch.extract(first_file)

        while self.listener.extract_completed.call_count < 1:
            pass

        self.dispatch.extract(second_file)

        while self.listener.extract_completed.call_count < 2:
            pass

        self.assertEqual([
            os.path.join(self.local_path, "aaa"),
            os.path.join(self.local_path, "bbb")
        ], processed_archives)
        self.assertTrue(self.dispatch._ExtractDispatch__worker_thread.is_alive())

    @pytest.mark.timeout(5)
    def test_worker_survives_unexpected_extraction_error(self):
        self.mock_is_archive.return_value = True
        self.mock_extract_archive.side_effect = [RuntimeError("unexpected"), None]
        self.dispatch.add_listener(self.listener)

        first_file = ModelFile("first", False)
        first_file.local_size = 100
        second_file = ModelFile("second", False)
        second_file.local_size = 100
        self.dispatch.extract(first_file)

        while self.listener.extract_failed.call_count < 1:
            time.sleep(0.01)
        self.assertTrue(self.dispatch._ExtractDispatch__worker_thread.is_alive())

        self.dispatch.extract(second_file)
        while self.listener.extract_completed.call_count < 1:
            time.sleep(0.01)

        self.assertEqual(1, self.listener.extract_failed.call_count)
        self.assertEqual(1, self.listener.extract_completed.call_count)
