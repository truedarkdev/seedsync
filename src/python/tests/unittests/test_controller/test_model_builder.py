# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import os
import sys
import unittest
from unittest.mock import patch
from datetime import datetime

from system import SystemFile
from lftp import LftpJobStatus
from model import ModelError, ModelFile, Model
from controller import ModelBuilder
from controller.model_builder import _RecentLiveTransferSnapshot
from controller.extract import ExtractStatus
from controller.validate import ValidateStatus


class TestModelBuilder(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger(TestModelBuilder.__name__)
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)
        self.model_builder = ModelBuilder()
        self.model_builder.set_base_logger(logger)

    def __build_test_model_children_tree_1(self) -> Model:
        """Build a test model for children testing"""
        self.model_builder.clear()

        r_a = SystemFile("a", 1024, True)
        r_aa = SystemFile("aa", 512, False)
        r_a.add_child(r_aa)
        r_ab = SystemFile("ab", 512, False)
        r_a.add_child(r_ab)
        r_b = SystemFile("b", 3090, True)
        r_ba = SystemFile("ba", 2048, True)
        r_b.add_child(r_ba)
        r_baa = SystemFile("baa", 2048, False)
        r_ba.add_child(r_baa)
        r_bb = SystemFile("bb", 42, True)  # only in remote
        r_b.add_child(r_bb)
        r_bba = SystemFile("bba", 42, False)  # only in remote
        r_bb.add_child(r_bba)
        r_bd = SystemFile("bd", 1000, False)
        r_b.add_child(r_bd)
        r_c = SystemFile("c", 1234, False)  # only in remote
        r_d = SystemFile("d", 5678, True)  # only in remote
        r_da = SystemFile("da", 5678, False)  # only in remote
        r_d.add_child(r_da)

        l_a = SystemFile("a", 1024, True)
        l_aa = SystemFile("aa", 512, False)
        l_a.add_child(l_aa)
        l_ab = SystemFile("ab", 512, False)
        l_a.add_child(l_ab)
        l_b = SystemFile("b", 1611, True)
        l_ba = SystemFile("ba", 512, True)
        l_b.add_child(l_ba)
        l_baa = SystemFile("baa", 512, False)
        l_ba.add_child(l_baa)
        l_bc = SystemFile("bc", 99, True)  # only in local
        l_b.add_child(l_bc)
        l_bca = SystemFile("bca", 99, False)  # only in local
        l_bc.add_child(l_bca)
        l_bd = SystemFile("bd", 1000, False)
        l_b.add_child(l_bd)

        s_b = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "b", "")
        s_b.total_transfer_state = LftpJobStatus.TransferState(1611, 3090, 52, 10, 1000)
        s_b.add_active_file_transfer_state("ba/baa", LftpJobStatus.TransferState(512, 2048, 25, 5, 500))
        s_c = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "c", "")
        s_d = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "d", "")

        self.model_builder.set_remote_files([r_a, r_b, r_c, r_d])
        self.model_builder.set_local_files([l_a, l_b])
        self.model_builder.set_lftp_statuses([s_b, s_c, s_d])
        return self.model_builder.build_model()

    def test_build_file_names(self):
        remote_files = [SystemFile("a", 0, False), SystemFile("b", 0, False)]
        local_files = [SystemFile("b", 0, False), SystemFile("c", 0, False)]
        statuses = [LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", ""),
                    LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "d", "")]
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)
        self.model_builder.set_lftp_statuses(statuses)
        model = self.model_builder.build_model()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())

    def test_build_is_dir(self):
        # remote
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, True)])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

        # local
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 0, True)])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

        # statuses
        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

        # all three
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(False, model.get_file("a").is_dir)
        self.model_builder.set_remote_files([SystemFile("a", 0, True)])
        self.model_builder.set_local_files([SystemFile("a", 0, True)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(True, model.get_file("a").is_dir)

    def test_build_mismatch_is_dir(self):
        """Mismatching is_dir raises error"""
        # remote mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, True)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir"))

        # local mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, True)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir"))

        # status mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        ])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir"))

        # extracting mismatches
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", True, ExtractStatus.State.EXTRACTING)])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir between file and extract status"))

    def test_build_state(self):
        # Queued
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.QUEUED, model.get_file("a").state)

        # Downloading
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Downloading - remote only
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 0, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Default
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Default - local only
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Downloaded - local only after the remote file disappeared
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)

        # Staging-only local file should not be promoted by stale completion markers
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False, is_staging=True)])
        self.model_builder.set_downloaded_files({"archive.zip"})
        self.model_builder.set_extracted_files({"archive.zip"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("archive.zip").state)

        # Downloaded
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADED, model.get_file("a").state)

        # Deleted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DELETED, model.get_file("a").state)

        # Deleted but Queued
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.QUEUED, model.get_file("a").state)

        # Deleted but Downloading
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Deleted, then partially Downloaded
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_downloaded_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Downloaded, and Extracting
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTING, model.get_file("a").state)

        # Local-only, and Extracting
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTING, model.get_file("a").state)

        # Remote-only, and Extracting (unexpected: should fall-back to Default)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Extracting and Downloading/Queued (unexpected: should ignore Extracting)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Extracting and Deleted (unexpected: should ignore Extracting)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DELETED, model.get_file("a").state)

        # Downloaded+Extracted, but extracting again
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_extracted_files({"a"})
        self.model_builder.set_extract_statuses([ExtractStatus("a", False, ExtractStatus.State.EXTRACTING)])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTING, model.get_file("a").state)

        # Downloaded, and Extracted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("a").state)

        # Local-only, and Extracted
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Remote-only, and Extracted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("a").state)

        # Extracted, but Downloading/Queued (possible after deletion)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 50, False)])
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        ])
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        # Extracted and Deleted
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_downloaded_files({"a"})
        self.model_builder.set_extracted_files({"a"})
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DELETED, model.get_file("a").state)

    def test_build_state_keeps_final_root_local_only_file_completed_from_persisted_markers(self):
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_downloaded_files({"archive.zip"})
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("archive.zip").state)

    def test_build_state_does_not_promote_staging_only_root_file_from_remote_size_match(self):
        self.model_builder.set_remote_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False, is_staging=True)])
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.DEFAULT, model.get_file("archive.zip").state)

    def test_build_state_does_not_promote_staging_only_child_file_from_remote_size_match(self):
        remote_root = SystemFile("folder", 100, True)
        remote_child = SystemFile("archive.zip", 100, False)
        remote_root.add_child(remote_child)
        local_root = SystemFile("folder", 100, True)
        local_child = SystemFile("archive.zip", 100, False, is_staging=True)
        local_root.add_child(local_child)

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        model = self.model_builder.build_model()

        built_root = model.get_file("folder")
        self.assertEqual(ModelFile.State.DEFAULT, built_root.state)
        self.assertEqual(ModelFile.State.DEFAULT, built_root.get_children()[0].state)

    def test_build_state_keeps_final_root_remote_size_match_completed(self):
        self.model_builder.set_remote_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_local_files([SystemFile("archive.zip", 100, False)])
        self.model_builder.set_extracted_files({"archive.zip"})

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.EXTRACTED, model.get_file("archive.zip").state)

    def test_build_remote_size(self):
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").remote_size)

        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").remote_size)

        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").remote_size)

    def test_build_remote_size_from_status_is_ignored(self):
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, 12345, None, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").remote_size)

    def test_build_local_size(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").local_size)

        self.model_builder.clear()
        self.model_builder.set_lftp_statuses([
            LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        ])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").local_size)

    def test_build_local_size_from_status_is_ignored(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").local_size)

    def test_build_local_size_downloading(self):
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

    def test_build_download_progress(self):
        remote_root = SystemFile("a", 100, True)
        remote_child = SystemFile("aa", 100, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("a", 24, True)
        local_child = SystemFile("aa", 24, False)
        local_root.add_child(local_child)

        s = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(24, 100, 60, 1000, 5)
        s.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(24, 100, 60, 500, 3))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        m_a_ch = {m.name: m for m in m_a.get_children()}

        self.assertEqual(24, m_a.local_size)
        self.assertEqual(100, m_a.remote_size)
        self.assertEqual(60, m_a.download_progress)
        self.assertEqual(60, m_a_ch["aa"].download_progress)

    def test_build_download_progress_fractional_percent_local(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 25, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(25, 100, 0.25, 1000, 5)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(25, model.get_file("a").download_progress)

    def test_build_download_progress_percent_local_one_is_literal_percent(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 1, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(1, 100, 1.0, 1000, 5)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(1, model.get_file("a").download_progress)

    def test_build_recent_live_transfer_snapshot_handoff(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 950, False)])
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(975, 1000, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(975, file_a.transferred_size)
        self.assertEqual(97, file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(975, file_a.transferred_size)
        self.assertEqual(97, file_a.download_progress)
        self.assertTrue(self.model_builder.has_changes())

        self.model_builder.set_local_files([SystemFile("a", 975, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(975, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_rekeys_legacy_alias_to_canonical_file_id(self):
        self.model_builder.clear()
        qualified_file_id = ModelFile.build_file_id("dup", "movies")
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile("dup", 900, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["dup"] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=950,
            percent_local=95,
            speed=1000,
            eta=5
        )

        model = self.model_builder.build_model()
        built_file = model.get_file(qualified_file_id)

        self.assertEqual(ModelFile.State.DOWNLOADING, built_file.state)
        self.assertEqual(950, built_file.transferred_size)
        self.assertEqual(95, built_file.download_progress)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn("dup", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_build_recent_live_transfer_child_snapshot_handoff_uses_real_file_id(self):
        remote_root = SystemFile("a", 1000, True)
        remote_child = SystemFile("aa", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("a", 650, True)
        local_child = SystemFile("aa", 650, False)
        local_root.add_child(local_child)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        built_child = model.get_file("a").get_children()[0]
        self.assertEqual(ModelFile.build_file_id(os.path.join("a", "aa"), None), built_child.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, built_child.state)
        self.assertEqual(750, built_child.transferred_size)
        self.assertEqual(75, built_child.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        built_child = model.get_file("a").get_children()[0]
        self.assertEqual(ModelFile.build_file_id(os.path.join("a", "aa"), None), built_child.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, built_child.state)
        self.assertEqual(750, built_child.transferred_size)
        self.assertEqual(75, built_child.download_progress)
        self.assertTrue(self.model_builder.has_changes())

        caught_up_local_root = SystemFile("a", 750, True)
        caught_up_local_child = SystemFile("aa", 750, False)
        caught_up_local_root.add_child(caught_up_local_child)
        self.model_builder.set_local_files([caught_up_local_root])
        model = self.model_builder.build_model()
        built_child = model.get_file("a").get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, built_child.state)
        self.assertEqual(750, built_child.transferred_size)
        self.assertIsNone(built_child.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_without_size_local_is_not_retained(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(None, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_is_evicted_when_file_disappears(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 700, False)])

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)

        self.model_builder.set_lftp_statuses([])
        self.model_builder.set_remote_files([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_is_not_applied_to_queued_state(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        self.assertEqual(750, model.get_file("a").transferred_size)

        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_name_entry_preserves_retained_transfer_metrics_without_active_state(self):
        self.model_builder.clear()
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "movies"
        remote_file.path_pair_name = "Movies"
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "movies"
        local_file.path_pair_name = "Movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_stopped_files({"dup"})

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "dup", "")
        status.path_pair_id = "movies"
        status.path_pair_name = "Movies"
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(ModelFile.build_file_id("dup", "movies"))
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertIsNone(file_dup.downloading_speed)
        self.assertIsNone(file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_status_only_stopped_file_name_entry_preserves_retained_transfer_metrics(self):
        self.model_builder.clear()
        self.model_builder.set_stopped_files({"dup"})

        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "dup", "")
        status.path_pair_id = "movies"
        status.path_pair_name = "Movies"
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(ModelFile.build_file_id("dup", "movies"))
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertIsNone(file_dup.downloading_speed)
        self.assertIsNone(file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_id_entry_preserves_retained_snapshot_with_legacy_status_root_id(self):
        self.model_builder.clear()
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "movies"
        remote_file.path_pair_name = "Movies"
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "movies"
        local_file.path_pair_name = "Movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        file_id = ModelFile.build_file_id("dup", "movies")
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[file_id] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=750,
            percent_local=75,
            speed=1000,
            eta=5
        )
        self.model_builder.set_stopped_files({ModelFile.build_file_id("dup", "movies")})

        model = self.model_builder.build_model()
        file_dup = model.get_file(file_id)
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertIsNone(file_dup.downloading_speed)
        self.assertIsNone(file_dup.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_id_entry_does_not_suppress_other_path_pair_snapshot(self):
        self.model_builder.clear()
        remote_file = SystemFile("dup", 1000, False)
        remote_file.path_pair_id = "tv"
        remote_file.path_pair_name = "TV"
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "tv"
        local_file.path_pair_name = "TV"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        file_id = ModelFile.build_file_id("dup", "tv")
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[file_id] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=750,
            percent_local=75,
            speed=1000,
            eta=5
        )
        self.model_builder.set_stopped_files({ModelFile.build_file_id("dup", "movies")})

        model = self.model_builder.build_model()
        file_dup = model.get_file(file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)
        self.assertEqual(1000, file_dup.downloading_speed)
        self.assertEqual(5, file_dup.eta)
        self.assertTrue(self.model_builder.has_changes())

    def test_build_stopped_staging_file_preserves_retained_snapshot_when_local_size_looks_complete(self):
        self.model_builder.clear()
        remote_file = SystemFile("a", 1000, False)
        local_file = SystemFile("a", 1000, False, is_staging=True)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=1000,
            eta=5
        )
        self.model_builder.set_stopped_files({"a"})

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(65, file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_staging_file_does_not_use_local_size_as_transferred_size_fallback(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False, is_staging=True)])

        model = self.model_builder.build_model()

        self.assertIsNone(model.get_file("a").transferred_size)

    def test_build_staging_child_without_transfer_bytes_does_not_break_parent_rollup(self):
        self.model_builder.clear()
        remote_root = SystemFile("root", 1000, True)
        remote_child = SystemFile("child", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("root", 1000, True)
        local_child = SystemFile("child", 1000, False, is_staging=True)
        local_root.add_child(local_child)

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])

        model = self.model_builder.build_model()
        built_root = model.get_file("root")
        built_child = built_root.get_children()[0]

        self.assertEqual(0, built_root.transferred_size)
        self.assertIsNone(built_child.transferred_size)

    def test_build_stopped_final_root_file_promotes_to_downloaded_when_authoritative_local_is_complete(self):
        self.model_builder.clear()
        remote_file = SystemFile("a", 1000, False)
        local_file = SystemFile("a", 1000, False)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=1000,
            eta=5
        )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )
        self.model_builder.set_stopped_files({"a"})

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertNotIn("a", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn("a", self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_file_prefers_retained_snapshot_over_larger_local_scan_size(self):
        self.model_builder.clear()
        remote_file = SystemFile("verifier-stop-regression-1g.bin", 1073741824, False)
        local_file = SystemFile("verifier-stop-regression-1g.bin", 1067800592, False)
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["verifier-stop-regression-1g.bin"] = \
            _RecentLiveTransferSnapshot(
                root_file_id="verifier-stop-regression-1g.bin",
                size_local=1044601281,
                percent_local=97,
                speed=None,
                eta=None
            )
        self.model_builder.set_stopped_files({"verifier-stop-regression-1g.bin"})

        model = self.model_builder.build_model()
        file_bin = model.get_file("verifier-stop-regression-1g.bin")
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1067800592, file_bin.local_size)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_resumed_running_state_keeps_retained_stopped_snapshot_until_progress_catches_up(self):
        self.model_builder.clear()
        file_name = "verifier-stop-regression-1g.bin"
        self.model_builder.set_remote_files([SystemFile(file_name, 1073741824, False)])
        self.model_builder.set_local_files([SystemFile(file_name, 1067800592, False)])
        self.model_builder.set_stopped_files({file_name})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.QUEUED, file_bin.state)
        self.assertEqual(1067800592, file_bin.transferred_size)
        self.assertIsNone(file_bin.download_progress)

        regressing_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        regressing_resume_status.total_transfer_state = LftpJobStatus.TransferState(1033895936, 1073741824, 96, 1000, 5)
        self.model_builder.set_lftp_statuses([regressing_resume_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertEqual(1000, file_bin.downloading_speed)
        self.assertEqual(5, file_bin.eta)

        caught_up_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_resume_status.total_transfer_state = LftpJobStatus.TransferState(1058013184, 1073741824, 99, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_resume_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_bin.state)
        self.assertEqual(1058013184, file_bin.transferred_size)
        self.assertEqual(99, file_bin.download_progress)

    def test_build_resumed_running_state_keeps_retained_floor_when_resume_uses_equivalent_root_alias(self):
        self.model_builder.clear()
        file_name = "verifier-stop-alias-regression.bin"
        remote_file = SystemFile(file_name, 1073741824, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 1067800592, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        qualified_file_id = ModelFile.build_file_id(file_name, "movies")
        self.model_builder.set_stopped_files({qualified_file_id})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        stopped_status.path_pair_id = "movies"
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(1044601281, 1073741824, 97, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DEFAULT, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[qualified_file_id].root_file_id = file_name

        self.model_builder.set_stopped_files(set())
        self.model_builder.set_remote_files([])
        self.model_builder.set_local_files([])

        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, file_name, "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.QUEUED, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        resume_status.total_transfer_state = LftpJobStatus.TransferState(1033895936, 1073741824, 96, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_bin.state)
        self.assertEqual(1044601281, file_bin.transferred_size)
        self.assertEqual(97, file_bin.download_progress)
        self.assertEqual(1000, file_bin.downloading_speed)
        self.assertEqual(5, file_bin.eta)

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(1058013184, 1073741824, 99, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        model = self.model_builder.build_model()
        file_bin = model.get_file(file_name)
        self.assertEqual(1058013184, file_bin.transferred_size)
        self.assertEqual(99, file_bin.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_alias_reuse_skips_ambiguous_duplicate_path_pair_candidates(self):
        self.model_builder.clear()
        file_name = "dup"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[movies_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[tv_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=850,
                percent_local=85,
                speed=None,
                eta=None
            )

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        resume_status.total_transfer_state = LftpJobStatus.TransferState(600, 1000, 60, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(file_name)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_dup.state)
        self.assertEqual(600, file_dup.transferred_size)
        self.assertEqual(60, file_dup.download_progress)
        self.assertIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_prefers_current_file_id_over_alias_when_both_exist(self):
        self.model_builder.clear()
        file_name = "dup"
        qualified_file_id = ModelFile.build_file_id(file_name, "movies")
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 650, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[file_name] = _RecentLiveTransferSnapshot(
            root_file_id=file_name,
            size_local=900,
            percent_local=90,
            speed=None,
            eta=None
        )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[qualified_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        resume_status.path_pair_id = "movies"
        resume_status.total_transfer_state = LftpJobStatus.TransferState(700, 1000, 70, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DOWNLOADING, file_dup.state)
        self.assertEqual(750, file_dup.transferred_size)
        self.assertEqual(75, file_dup.download_progress)

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.path_pair_id = "movies"
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(760, 1000, 76, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(qualified_file_id)
        self.assertEqual(760, file_dup.transferred_size)
        self.assertEqual(76, file_dup.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(file_name, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_catch_up_does_not_evict_other_duplicate_path_pair_retained_snapshot(self):
        self.model_builder.clear()
        file_name = "dup"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 650, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[movies_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[tv_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=850,
                percent_local=85,
                speed=None,
                eta=None
            )

        caught_up_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        caught_up_status.path_pair_id = "movies"
        caught_up_status.total_transfer_state = LftpJobStatus.TransferState(760, 1000, 76, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(movies_file_id)
        self.assertEqual(760, file_dup.transferred_size)
        self.assertEqual(76, file_dup.download_progress)
        self.assertNotIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_reset_does_not_evict_other_duplicate_path_pair_retained_snapshot(self):
        self.model_builder.clear()
        file_name = "dup"
        movies_file_id = ModelFile.build_file_id(file_name, "movies")
        tv_file_id = ModelFile.build_file_id(file_name, "tv")
        remote_file = SystemFile(file_name, 1000, False)
        remote_file.path_pair_id = "movies"
        local_file = SystemFile(file_name, 0, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_remote_files([remote_file])
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[movies_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=750,
                percent_local=75,
                speed=None,
                eta=None
            )
        self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots[tv_file_id] = \
            _RecentLiveTransferSnapshot(
                root_file_id=file_name,
                size_local=850,
                percent_local=85,
                speed=None,
                eta=None
            )

        reset_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, file_name, "")
        reset_status.path_pair_id = "movies"
        reset_status.total_transfer_state = LftpJobStatus.TransferState(0, 1000, 0, 1000, 5)
        self.model_builder.set_lftp_statuses([reset_status])

        model = self.model_builder.build_model()
        file_dup = model.get_file(movies_file_id)
        self.assertEqual(0, file_dup.transferred_size)
        self.assertEqual(0, file_dup.download_progress)
        self.assertNotIn(movies_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)
        self.assertIn(tv_file_id, self.model_builder._ModelBuilder__retained_stopped_transfer_snapshots)

    def test_build_resumed_running_state_keeps_percent_floor_when_size_has_caught_up(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 750, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        resume_status.total_transfer_state = LftpJobStatus.TransferState(800, 1000, 74, 1000, 5)
        self.model_builder.set_lftp_statuses([resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(800, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

        caught_up_percent_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        caught_up_percent_status.total_transfer_state = LftpJobStatus.TransferState(810, 1000, 76, 1000, 5)
        self.model_builder.set_lftp_statuses([caught_up_percent_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(810, file_a.transferred_size)
        self.assertEqual(76, file_a.download_progress)

    def test_build_resumed_queued_state_keeps_retained_stopped_floor_for_non_authoritative_local_progress(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False, is_staging=True)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_resumed_queued_state_keeps_retained_stopped_floor_for_authoritative_local_progress(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 750, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 78, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(78, file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_resumed_queued_state_allows_explicit_zero_reset_after_stop(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, file_a.state)
        self.assertEqual(0, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_resumed_running_state_allows_clear_reset_signal_after_stop(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        self.model_builder.set_local_files([SystemFile("a", 0, False)])
        reset_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        reset_resume_status.total_transfer_state = LftpJobStatus.TransferState(0, 1000, 0, 1000, 5)
        self.model_builder.set_lftp_statuses([reset_resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(0, file_a.transferred_size)
        self.assertEqual(0, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

    def test_build_resumed_running_state_keeps_retained_floor_for_near_zero_non_zero_percent(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        near_zero_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        near_zero_resume_status.total_transfer_state = LftpJobStatus.TransferState(100, 1000, 0.004, 1000, 5)
        self.model_builder.set_lftp_statuses([near_zero_resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

    def test_build_resumed_running_state_keeps_retained_floor_for_transient_smaller_authoritative_local_sample(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        self.model_builder.set_stopped_files({"a"})

        stopped_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        stopped_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([stopped_status])
        self.model_builder.build_model()

        self.model_builder.set_stopped_files(set())
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_lftp_statuses([queued_status])
        self.model_builder.build_model()

        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        regressing_resume_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        regressing_resume_status.total_transfer_state = LftpJobStatus.TransferState(100, 1000, 10, 1000, 5)
        self.model_builder.set_lftp_statuses([regressing_resume_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertEqual(75, file_a.download_progress)
        self.assertEqual(1000, file_a.downloading_speed)
        self.assertEqual(5, file_a.eta)

    def test_build_running_state_promotes_to_downloaded_when_authoritative_local_file_is_complete(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])

        running_status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(990, 1000, 99, 1000, 0)
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, file_a.state)
        self.assertEqual(1000, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertIsNone(file_a.downloading_speed)
        self.assertIsNone(file_a.eta)

    def test_build_stopped_file_retains_snapshot_across_remote_missing_then_reappears(self):
        self.model_builder.clear()
        local_file = SystemFile("a", 650, False)
        self.model_builder.set_local_files([local_file])
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )
        self.model_builder.set_stopped_files({"a"})

        first_model = self.model_builder.build_model()
        first_file = first_model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, first_file.state)
        self.assertEqual(650, first_file.local_size)
        self.assertEqual(650, first_file.transferred_size)
        self.assertEqual(65, first_file.download_progress)

        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])

        second_model = self.model_builder.build_model()
        second_file = second_model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, second_file.state)
        self.assertEqual(1000, second_file.remote_size)
        self.assertEqual(650, second_file.transferred_size)
        self.assertEqual(65, second_file.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_evict_recent_live_transfer_snapshots_missing_roots_preserves_stopped_snapshot(self):
        self.model_builder.clear()
        local_file = SystemFile("a", 650, False)
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_stopped_files({"a"})
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )

        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(65, file_a.download_progress)
        self.assertIn("a", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_evict_recent_live_transfer_snapshots_missing_roots_preserves_stopped_and_evicts_non_stopped(self):
        self.model_builder.clear()
        stopped_local_file = SystemFile("a", 650, False)
        running_local_file = SystemFile("b", 400, False)
        self.model_builder.set_local_files([stopped_local_file, running_local_file])
        self.model_builder.set_stopped_files({"a"})
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["a"] = _RecentLiveTransferSnapshot(
            root_file_id="a",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots["b"] = _RecentLiveTransferSnapshot(
            root_file_id="b",
            size_local=400,
            percent_local=40,
            speed=None,
            eta=None
        )

        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())

        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        file_b = model.get_file("b")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(650, file_a.transferred_size)
        self.assertEqual(65, file_a.download_progress)
        self.assertEqual(ModelFile.State.DEFAULT, file_b.state)
        self.assertIsNone(file_b.transferred_size)
        self.assertIsNone(file_b.download_progress)
        self.assertIn("a", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)
        self.assertNotIn("b", self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_evict_recent_live_transfer_snapshots_missing_roots_alias_stopped_snapshot_is_removed_after_protection_clears(self):
        self.model_builder.clear()
        qualified_file_id = ModelFile.build_file_id("dup", "movies")
        local_file = SystemFile("dup", 650, False)
        local_file.path_pair_id = "movies"
        self.model_builder.set_local_files([local_file])
        self.model_builder.set_stopped_files({qualified_file_id})
        self.model_builder._ModelBuilder__recent_live_transfer_snapshots[qualified_file_id] = _RecentLiveTransferSnapshot(
            root_file_id="dup",
            size_local=650,
            percent_local=65,
            speed=None,
            eta=None
        )

        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())
        self.assertIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

        self.model_builder.set_stopped_files(set())
        self.model_builder.evict_recent_live_transfer_snapshots_missing_roots(set())

        model = self.model_builder.build_model()
        file_dup = model.get_file(qualified_file_id)
        self.assertEqual(ModelFile.State.DEFAULT, file_dup.state)
        self.assertIsNone(file_dup.transferred_size)
        self.assertIsNone(file_dup.download_progress)
        self.assertNotIn(qualified_file_id, self.model_builder._ModelBuilder__recent_live_transfer_snapshots)

    def test_build_stopped_directory_name_entry_suppresses_child_live_transfer_state(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, False)
        local_root.add_child(local_child)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({"dup"})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]

        self.assertEqual(ModelFile.State.DEFAULT, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(650, child.transferred_size)
        self.assertIsNone(child.download_progress)
        self.assertIsNone(child.downloading_speed)
        self.assertIsNone(child.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_directory_name_entry_suppresses_nested_descendant_live_transfer_state(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({"dup"})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]

        self.assertEqual(ModelFile.State.DEFAULT, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DEFAULT, grandchild.state)
        self.assertEqual(650, grandchild.transferred_size)
        self.assertIsNone(grandchild.download_progress)
        self.assertIsNone(grandchild.downloading_speed)
        self.assertIsNone(grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_stopped_descendant_file_id_suppresses_nested_descendant_live_transfer_state(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({ModelFile.build_file_id(os.path.join("dup", "aa", "bb"), "movies")})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]

        self.assertEqual(ModelFile.State.DOWNLOADING, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DEFAULT, grandchild.state)
        self.assertEqual(650, grandchild.transferred_size)
        self.assertIsNone(grandchild.download_progress)
        self.assertIsNone(grandchild.downloading_speed)
        self.assertIsNone(grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_unrelated_legacy_stopped_name_does_not_suppress_matching_grandchild(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_stopped_files({"bb"})
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]

        self.assertEqual(ModelFile.State.DOWNLOADING, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DOWNLOADING, grandchild.state)
        self.assertEqual(750, grandchild.transferred_size)
        self.assertEqual(75, grandchild.download_progress)
        self.assertEqual(1000, grandchild.downloading_speed)
        self.assertEqual(5, grandchild.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_descendant_snapshot_ignores_unrelated_legacy_stopped_name(self):
        remote_root = SystemFile("dup", 1000, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("aa", 1000, True)
        remote_root.add_child(remote_child)
        remote_grandchild = SystemFile("bb", 1000, False)
        remote_child.add_child(remote_grandchild)

        local_root = SystemFile("dup", 650, True)
        local_root.path_pair_id = "movies"
        local_root.path_pair_name = "Movies"
        local_child = SystemFile("aa", 650, True)
        local_root.add_child(local_child)
        local_grandchild = SystemFile("bb", 650, False)
        local_child.add_child(local_grandchild)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "dup", "")
        running_status.path_pair_id = "movies"
        running_status.path_pair_name = "Movies"
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa/bb", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        grandchild = root.get_children()[0].get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, grandchild.state)
        self.assertEqual(750, grandchild.transferred_size)
        self.assertEqual(75, grandchild.download_progress)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        self.model_builder.set_stopped_files({"bb"})

        model = self.model_builder.build_model()
        root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        child = root.get_children()[0]
        grandchild = child.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, root.state)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(ModelFile.State.DOWNLOADING, grandchild.state)
        self.assertEqual(750, grandchild.transferred_size)
        self.assertEqual(75, grandchild.download_progress)
        self.assertEqual(1000, grandchild.downloading_speed)
        self.assertEqual(5, grandchild.eta)
        self.assertTrue(self.model_builder.has_changes())

    def test_build_recent_live_transfer_snapshot_survives_until_local_catches_up(self):
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 1000, False)])
        self.model_builder.set_local_files([SystemFile("a", 650, False)])
        status = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        self.model_builder.set_lftp_statuses([status])

        model = self.model_builder.build_model()
        self.assertEqual(750, model.get_file("a").transferred_size)

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.DOWNLOADING, model.get_file("a").state)
        self.assertEqual(750, model.get_file("a").transferred_size)
        self.assertTrue(self.model_builder.has_changes())

        self.model_builder.set_local_files([SystemFile("a", 750, False)])
        model = self.model_builder.build_model()
        file_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, file_a.state)
        self.assertEqual(750, file_a.transferred_size)
        self.assertIsNone(file_a.download_progress)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_recent_live_transfer_child_snapshot_is_suppressed_by_queued_root_state(self):
        remote_root = SystemFile("a", 1000, True)
        remote_child = SystemFile("aa", 1000, False)
        remote_root.add_child(remote_child)

        local_root = SystemFile("a", 650, True)
        local_child = SystemFile("aa", 650, False)
        local_root.add_child(local_child)

        running_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        running_status.total_transfer_state = LftpJobStatus.TransferState(750, 1000, 75, 1000, 5)
        running_status.add_active_file_transfer_state("aa", LftpJobStatus.TransferState(750, 1000, 75, 1000, 5))
        queued_status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")

        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([running_status])
        self.model_builder.build_model()

        self.model_builder.set_lftp_statuses([queued_status])

        model = self.model_builder.build_model()
        root = model.get_file("a")
        child = root.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, child.state)
        self.assertEqual(650, child.transferred_size)
        self.assertIsNone(child.download_progress)
        self.assertIsNone(child.downloading_speed)
        self.assertIsNone(child.eta)
        self.assertFalse(self.model_builder.has_changes())

        self.model_builder.set_lftp_statuses([])
        model = self.model_builder.build_model()
        root = model.get_file("a")
        child = root.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, root.state)
        self.assertEqual(650, root.transferred_size)
        self.assertIsNone(root.download_progress)
        self.assertIsNone(root.downloading_speed)
        self.assertIsNone(root.eta)
        self.assertEqual(ModelFile.State.DEFAULT, child.state)
        self.assertEqual(650, child.transferred_size)
        self.assertIsNone(child.download_progress)
        self.assertIsNone(child.downloading_speed)
        self.assertIsNone(child.eta)
        self.assertFalse(self.model_builder.has_changes())

    def test_build_downloading_state_is_retained(self):
        # downloading files latest info should be retained even after
        # they have stopped downloading
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        self.model_builder.set_active_files([SystemFile("a", 99, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

        # set active files to empty
        self.model_builder.set_active_files([])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(12345, 1000, 0.25, None, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(99, model.get_file("a").local_size)

    def test_build_downloading_speed(self):
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 1234, None)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(1234, model.get_file("a").downloading_speed)

        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").downloading_speed)

        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").downloading_speed)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").downloading_speed)

    def test_build_eta(self):
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, None, 4567)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(4567, model.get_file("a").eta)

        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

    def test_build_estimated_eta(self):
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 100, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(10, model.get_file("a").eta)

        # round up
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 133, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(8, model.get_file("a").eta)

        # round up
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 133, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1999, False)])
        model = self.model_builder.build_model()
        self.assertEqual(1, model.get_file("a").eta)

        # zero downloading speed
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 0, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        # remote size unavailable
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 100, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_local_files([SystemFile("a", 1000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").eta)

        # finished
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 200, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 2000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(0, model.get_file("a").eta)

        # local size larger than remote
        self.model_builder.clear()
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(None, None, None, 200, None)
        self.model_builder.set_lftp_statuses([s])
        self.model_builder.set_remote_files([SystemFile("a", 2000, False)])
        self.model_builder.set_local_files([SystemFile("a", 3000, False)])
        model = self.model_builder.build_model()
        self.assertEqual(0, model.get_file("a").eta)

    def test_build_children_names(self):
        model = self.__build_test_model_children_tree_1()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        self.assertEqual({"aa", "ab"}, m_a_ch.keys())
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        self.assertEqual({"ba", "bb", "bc", "bd"}, m_b_ch.keys())
        m_ba_ch = {m.name: m for m in m_b_ch["ba"].get_children()}
        self.assertEqual({"baa"}, m_ba_ch.keys())
        m_baa_ch = {m.name: m for m in m_ba_ch["baa"].get_children()}
        self.assertEqual(0, len(m_baa_ch.keys()))
        m_bb_ch = {m.name: m for m in m_b_ch["bb"].get_children()}
        self.assertEqual({"bba"}, m_bb_ch.keys())
        m_bba_ch = {m.name: m for m in m_bb_ch["bba"].get_children()}
        self.assertEqual(0, len(m_bba_ch.keys()))
        m_bc_ch = {m.name: m for m in m_b_ch["bc"].get_children()}
        self.assertEqual({"bca"}, m_bc_ch.keys())
        m_bca_ch = {m.name: m for m in m_bc_ch["bca"].get_children()}
        self.assertEqual(0, len(m_bca_ch.keys()))
        m_c_ch = {m.name: m for m in model.get_file("c").get_children()}
        self.assertEqual(0, len(m_c_ch.keys()))
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        self.assertEqual({"da"}, m_d_ch.keys())
        m_da_ch = {m.name: m for m in m_d_ch["da"].get_children()}
        self.assertEqual(0, len(m_da_ch.keys()))

    def test_build_children_is_dir(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(True, m_a.is_dir)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(False, m_aa.is_dir)
        m_ab = m_a_ch["ab"]
        self.assertEqual(False, m_ab.is_dir)
        m_b = model.get_file("b")
        self.assertEqual(True, m_b.is_dir)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(True, m_ba.is_dir)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(False, m_baa.is_dir)
        m_bb = m_b_ch["bb"]
        self.assertEqual(True, m_bb.is_dir)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(False, m_bba.is_dir)
        m_bc = m_b_ch["bc"]
        self.assertEqual(True, m_bc.is_dir)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(False, m_bca.is_dir)
        m_bd = m_b_ch["bd"]
        self.assertEqual(False, m_bd.is_dir)
        m_c = model.get_file("c")
        self.assertEqual(False, m_c.is_dir)
        m_d = model.get_file("d")
        self.assertEqual(True, m_d.is_dir)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(False, m_da.is_dir)

    def test_build_children_mismatch_is_dir(self):
        """Mismatching is_dir in a child raises error"""
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        l_a = SystemFile("a", 0, True)
        l_aa = SystemFile("aa", 0, False)
        l_a.add_child(l_aa)
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])
        with self.assertRaises(ModelError) as context:
            self.model_builder.build_model()
        self.assertTrue(str(context.exception).startswith("Mismatch in is_dir between child"))

    def test_build_children_sizes(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual((1024, 1024, 1024), (m_a.remote_size, m_a.local_size, m_a.transferred_size))
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual((512, 512, 512), (m_aa.remote_size, m_aa.local_size, m_aa.transferred_size))
        m_ab = m_a_ch["ab"]
        self.assertEqual((512, 512, 512), (m_ab.remote_size, m_ab.local_size, m_ab.transferred_size))
        m_b = model.get_file("b")
        self.assertEqual((3090, 1611, 1611), (m_b.remote_size, m_b.local_size, m_b.transferred_size))
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual((2048, 512, 512), (m_ba.remote_size, m_ba.local_size, m_ba.transferred_size))
        m_baa = m_ba.get_children()[0]
        self.assertEqual((2048, 512, 512), (m_baa.remote_size, m_baa.local_size, m_baa.transferred_size))
        m_bb = m_b_ch["bb"]
        self.assertEqual((42, None, None), (m_bb.remote_size, m_bb.local_size, m_bb.transferred_size))
        m_bba = m_bb.get_children()[0]
        self.assertEqual((42, None, None), (m_bba.remote_size, m_bba.local_size, m_bba.transferred_size))
        m_bc = m_b_ch["bc"]
        self.assertEqual((None, 99, None), (m_bc.remote_size, m_bc.local_size, m_bc.transferred_size))
        m_bca = m_bc.get_children()[0]
        self.assertEqual((None, 99, None), (m_bca.remote_size, m_bca.local_size, m_bca.transferred_size))
        m_bd = m_b_ch["bd"]
        self.assertEqual((1000, 1000, 1000), (m_bd.remote_size, m_bd.local_size, m_bd.transferred_size))
        m_c = model.get_file("c")
        self.assertEqual((1234, None, None), (m_c.remote_size, m_c.local_size, m_c.transferred_size))
        m_d = model.get_file("d")
        self.assertEqual((5678, None, None), (m_d.remote_size, m_d.local_size, m_d.transferred_size))
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual((5678, None, None), (m_da.remote_size, m_da.local_size, m_da.transferred_size))

    def test_build_children_state_default(self):
        """File only exists remotely"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        self.model_builder.set_remote_files([r_a])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_default_partial(self):
        """File is partially downloaded"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 150, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 100, False)
        l_a.add_child(l_ab)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_default_extra(self):
        """File only exists locally"""
        l_a = SystemFile("a", 150, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 100, False)
        l_a.add_child(l_ab)

        self.model_builder.set_local_files([l_a])
        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_downloaded_full(self):
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 300, True)
        l_aa = SystemFile("aa", 100, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 100, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)

    def test_build_children_state_downloaded_full_extra(self):
        """Fully downloaded but with an extra local-only file"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 400, True)
        l_aa = SystemFile("aa", 100, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 100, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)
        l_ac = SystemFile("ac", 100, True)  # local only
        l_a.add_child(l_ac)
        l_aca = SystemFile("aca", 100, False)  # local only
        l_ac.add_child(l_aca)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)
        l_ac = SystemFile("ac", 100, True)  # local only
        l_a.add_child(l_ac)
        l_aca = SystemFile("aca", 100, False)  # local only
        l_ac.add_child(l_aca)

    def test_build_children_state_downloaded_partial(self):
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 250, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)

    def test_build_children_state_downloaded_partial_extra(self):
        """Partially downloaded but with an extra local-only file"""
        r_a = SystemFile("a", 300, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 200, False)
        r_a.add_child(r_ab)

        l_a = SystemFile("a", 350, True)
        l_aa = SystemFile("aa", 50, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 50, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 200, False)
        l_a.add_child(l_ab)
        l_ac = SystemFile("ac", 100, True)  # local only
        l_a.add_child(l_ac)
        l_aca = SystemFile("aca", 100, False)  # local only
        l_ac.add_child(l_aca)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)
        m_ac = m_a_ch["ac"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ac.state)
        m_aca = m_ac.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aca.state)

    def test_build_children_state_default_remote_dir_without_remote_leaf_files(self):
        """Remote directories without remote files should not be marked downloaded"""
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)

        l_a = SystemFile("a", 100, True)
        l_aa = SystemFile("aa", 100, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 100, False)  # local only leaf
        l_aa.add_child(l_aaa)

        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DEFAULT, m_a.state)
        m_aa = m_a.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)

    def test_build_children_state_queued(self):
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 0, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 0, False)
        r_a.add_child(r_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.QUEUED, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.QUEUED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.QUEUED, m_ab.state)

    def test_build_children_state_downloading_1(self):
        # Child files are active
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 0, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 0, False)
        r_a.add_child(r_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        s_a.add_active_file_transfer_state("aa/aaa", LftpJobStatus.TransferState(None, None, None, None, None))
        s_a.add_active_file_transfer_state("ab", LftpJobStatus.TransferState(None, None, None, None, None))
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADING, m_ab.state)

    def test_build_children_state_downloading_2(self):
        # Child files are finished
        r_a = SystemFile("a", 150, True)
        r_aa = SystemFile("aa", 100, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 100, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 50, False)
        r_a.add_child(r_ab)

        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)

    def test_build_children_state_downloading_3(self):
        # Child files are queued
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        r_aaa = SystemFile("aaa", 0, False)
        r_aa.add_child(r_aaa)
        r_ab = SystemFile("ab", 0, False)
        r_a.add_child(r_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.QUEUED, m_ab.state)

    def test_build_children_state_downloading_4(self):
        # Child files are only present in local
        r_a = SystemFile("a", 0, True)
        r_aa = SystemFile("aa", 0, True)
        r_a.add_child(r_aa)
        l_a = SystemFile("a", 0, True)
        l_aa = SystemFile("aa", 0, True)
        l_a.add_child(l_aa)
        l_aaa = SystemFile("aaa", 0, False)
        l_aa.add_child(l_aaa)
        l_ab = SystemFile("ab", 0, False)
        l_a.add_child(l_ab)
        s_a = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "")
        self.model_builder.set_remote_files([r_a])
        self.model_builder.set_local_files([l_a])
        self.model_builder.set_lftp_statuses([s_a])

        model = self.model_builder.build_model()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DEFAULT, m_aa.state)
        m_aaa = m_aa.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_aaa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ab.state)

    def test_build_children_state_all(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(ModelFile.State.DOWNLOADED, m_a.state)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_aa.state)
        m_ab = m_a_ch["ab"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_ab.state)
        m_b = model.get_file("b")
        self.assertEqual(ModelFile.State.DOWNLOADING, m_b.state)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(ModelFile.State.DEFAULT, m_ba.state)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(ModelFile.State.DOWNLOADING, m_baa.state)
        m_bb = m_b_ch["bb"]
        self.assertEqual(ModelFile.State.DEFAULT, m_bb.state)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(ModelFile.State.QUEUED, m_bba.state)
        m_bc = m_b_ch["bc"]
        self.assertEqual(ModelFile.State.DEFAULT, m_bc.state)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(ModelFile.State.DEFAULT, m_bca.state)
        m_bd = m_b_ch["bd"]
        self.assertEqual(ModelFile.State.DOWNLOADED, m_bd.state)
        m_c = model.get_file("c")
        self.assertEqual(ModelFile.State.QUEUED, m_c.state)
        m_d = model.get_file("d")
        self.assertEqual(ModelFile.State.QUEUED, m_d.state)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(ModelFile.State.QUEUED, m_da.state)

    def test_build_children_downloading_speed(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(None, m_a.downloading_speed)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(None, m_aa.downloading_speed)
        m_ab = m_a_ch["ab"]
        self.assertEqual(None, m_ab.downloading_speed)
        m_b = model.get_file("b")
        self.assertEqual(10, m_b.downloading_speed)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(None, m_ba.downloading_speed)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(5, m_baa.downloading_speed)
        m_bb = m_b_ch["bb"]
        self.assertEqual(None, m_bb.downloading_speed)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(None, m_bba.downloading_speed)
        m_bc = m_b_ch["bc"]
        self.assertEqual(None, m_bc.downloading_speed)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(None, m_bca.downloading_speed)
        m_bd = m_b_ch["bd"]
        self.assertEqual(None, m_bd.downloading_speed)
        m_c = model.get_file("c")
        self.assertEqual(None, m_c.downloading_speed)
        m_d = model.get_file("d")
        self.assertEqual(None, m_d.downloading_speed)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(None, m_da.downloading_speed)

    def test_build_children_eta(self):
        model = self.__build_test_model_children_tree_1()
        m_a = model.get_file("a")
        self.assertEqual(None, m_a.eta)
        m_a_ch = {m.name: m for m in model.get_file("a").get_children()}
        m_aa = m_a_ch["aa"]
        self.assertEqual(None, m_aa.eta)
        m_ab = m_a_ch["ab"]
        self.assertEqual(None, m_ab.eta)
        m_b = model.get_file("b")
        self.assertEqual(1000, m_b.eta)
        m_b_ch = {m.name: m for m in model.get_file("b").get_children()}
        m_ba = m_b_ch["ba"]
        self.assertEqual(None, m_ba.eta)
        m_baa = m_ba.get_children()[0]
        self.assertEqual(500, m_baa.eta)
        m_bb = m_b_ch["bb"]
        self.assertEqual(None, m_bb.eta)
        m_bba = m_bb.get_children()[0]
        self.assertEqual(None, m_bba.eta)
        m_bc = m_b_ch["bc"]
        self.assertEqual(None, m_bc.eta)
        m_bca = m_bc.get_children()[0]
        self.assertEqual(None, m_bca.eta)
        m_bd = m_b_ch["bd"]
        self.assertEqual(None, m_bd.eta)
        m_c = model.get_file("c")
        self.assertEqual(None, m_c.eta)
        m_d = model.get_file("d")
        self.assertEqual(None, m_d.eta)
        m_d_ch = {m.name: m for m in model.get_file("d").get_children()}
        m_da = m_d_ch["da"]
        self.assertEqual(None, m_da.eta)

    @patch("controller.model_builder.Extract")
    def test_build_sets_is_extractable(self, mock_extract_module):
        mock_is_archive_fast = mock_extract_module.is_archive_fast
        is_archive_list = []

        def _is_archive_fast(name: str):
            return name in is_archive_list
        mock_is_archive_fast.side_effect = _is_archive_fast

        # Root local file
        self.model_builder.clear()
        is_archive_list = ["a"]
        self.model_builder.set_local_files([SystemFile("a", 10, False), SystemFile("b", 10, False)])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        self.assertFalse(model.get_file("b").is_extractable)

        # Root remote file
        self.model_builder.clear()
        is_archive_list = ["b"]
        self.model_builder.set_remote_files([SystemFile("a", 10, False), SystemFile("b", 10, False)])
        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("a").is_extractable)
        self.assertTrue(model.get_file("b").is_extractable)

        # Directory with archive
        self.model_builder.clear()
        is_archive_list = ["aa"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, False)
        a.add_child(aa)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        self.assertEqual("aa", model.get_file("a").get_children()[0].name)
        self.assertTrue(model.get_file("a").get_children()[0].is_extractable)

        # Directory with non-archive
        self.model_builder.clear()
        is_archive_list = ["aa"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("ab", 10, False)
        a.add_child(aa)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("a").is_extractable)
        self.assertEqual("ab", model.get_file("a").get_children()[0].name)
        self.assertFalse(model.get_file("a").get_children()[0].is_extractable)

        # Directory with archive and non-archive
        self.model_builder.clear()
        is_archive_list = ["ab"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, False)
        ab = SystemFile("ab", 10, False)
        a.add_child(aa)
        a.add_child(ab)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        a_children = {f.name: f for f in model.get_file("a").get_children()}
        self.assertFalse(a_children["aa"].is_extractable)
        self.assertTrue(a_children["ab"].is_extractable)

        # Directory with archive and non-archive sub-directories
        self.model_builder.clear()
        is_archive_list = ["aba"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, True)
        aaa = SystemFile("aaa", 10, False)
        ab = SystemFile("ab", 10, True)
        aba = SystemFile("aba", 10, False)
        a.add_child(aa)
        a.add_child(ab)
        aa.add_child(aaa)
        ab.add_child(aba)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertTrue(model.get_file("a").is_extractable)
        a_children = {f.name: f for f in model.get_file("a").get_children()}
        self.assertFalse(a_children["aa"].is_extractable)
        self.assertEqual("aaa", a_children["aa"].get_children()[0].name)
        self.assertFalse(a_children["aa"].get_children()[0].is_extractable)
        self.assertTrue(a_children["ab"].is_extractable)
        self.assertEqual("aba", a_children["ab"].get_children()[0].name)
        self.assertTrue(a_children["ab"].get_children()[0].is_extractable)

        # Directory name passes is_archive, but not file
        self.model_builder.clear()
        is_archive_list = ["a"]
        a = SystemFile("a", 10, True)
        aa = SystemFile("aa", 10, False)
        a.add_child(aa)
        self.model_builder.set_local_files([a])
        model = self.model_builder.build_model()
        self.assertFalse(model.get_file("a").is_extractable)
        self.assertEqual("aa", model.get_file("a").get_children()[0].name)
        self.assertFalse(model.get_file("a").get_children()[0].is_extractable)

    def test_build_transferred_size(self):
        # both remote and local
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        self.model_builder.set_local_files([SystemFile("a", 22, False)])
        model = self.model_builder.build_model()
        self.assertEqual(22, model.get_file("a").transferred_size)

        # remote but no local
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

        # local but no remote
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 22, False)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

        # local size larger than remote
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        self.model_builder.set_local_files([SystemFile("a", 55, False)])
        model = self.model_builder.build_model()
        self.assertEqual(42, model.get_file("a").transferred_size)

        # active download prefers live transfer bytes
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, False)])
        self.model_builder.set_local_files([SystemFile("a", 22, False)])
        s = LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.RUNNING, "a", "")
        s.total_transfer_state = LftpJobStatus.TransferState(33, 42, 0.75, 1000, 5)
        self.model_builder.set_lftp_statuses([s])
        model = self.model_builder.build_model()
        self.assertEqual(33, model.get_file("a").transferred_size)

        # downloading directories without an explicit live root value still
        # aggregate child live bytes
        self.model_builder.clear()
        remote_root = SystemFile("root", 100, True)
        remote_child = SystemFile("child", 100, False)
        remote_root.add_child(remote_child)
        local_root = SystemFile("root", 0, True)
        local_child = SystemFile("child", 0, False)
        local_root.add_child(local_child)
        status = LftpJobStatus(0, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "root", "")
        status.total_transfer_state = LftpJobStatus.TransferState(None, 100, None, 1000, 5)
        status.add_active_file_transfer_state("child", LftpJobStatus.TransferState(18, 100, 0.18, 500, 3))
        self.model_builder.set_remote_files([remote_root])
        self.model_builder.set_local_files([local_root])
        self.model_builder.set_lftp_statuses([status])
        model = self.model_builder.build_model()
        self.assertEqual(18, model.get_file("root").transferred_size)

        # both remote and local directory (but no children specified)
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, True)])
        self.model_builder.set_local_files([SystemFile("a", 22, True)])
        model = self.model_builder.build_model()
        self.assertEqual(0, model.get_file("a").transferred_size)

        # remote only directory
        self.model_builder.clear()
        self.model_builder.set_remote_files([SystemFile("a", 42, True)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

        # local only directory
        self.model_builder.clear()
        self.model_builder.set_local_files([SystemFile("a", 22, True)])
        model = self.model_builder.build_model()
        self.assertEqual(None, model.get_file("a").transferred_size)

    def test_build_local_created_timestamp(self):
        self.model_builder.set_local_files([
            SystemFile("a", 42, False, time_created=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").local_created_timestamp)
        self.assertIsNone(model.get_file("b").local_created_timestamp)

    def test_build_local_modified_timestamp(self):
        self.model_builder.set_local_files([
            SystemFile("a", 42, False, time_modified=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").local_modified_timestamp)
        self.assertIsNone(model.get_file("b").local_modified_timestamp)

    def test_build_remote_created_timestamp(self):
        self.model_builder.set_remote_files([
            SystemFile("a", 42, False, time_created=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").remote_created_timestamp)
        self.assertIsNone(model.get_file("b").remote_created_timestamp)

    def test_build_remote_modified_timestamp(self):
        self.model_builder.set_remote_files([
            SystemFile("a", 42, False, time_modified=datetime(2018, 11, 9, 21, 40, 18)),
            SystemFile("b", 42, False)
        ])
        model = self.model_builder.build_model()
        self.assertEqual(datetime(2018, 11, 9, 21, 40, 18),
                         model.get_file("a").remote_modified_timestamp)
        self.assertIsNone(model.get_file("b").remote_modified_timestamp)

    def test_rebuild(self):
        remote_files = [SystemFile("a", 0, False), SystemFile("b", 0, False)]
        local_files = [SystemFile("b", 0, False), SystemFile("c", 0, False)]
        statuses = [LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", ""),
                    LftpJobStatus(0, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "d", "")]
        self.model_builder.set_remote_files(remote_files)
        self.model_builder.set_local_files(local_files)
        self.model_builder.set_lftp_statuses(statuses)
        model = self.model_builder.build_model()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())

        self.assertFalse(self.model_builder.has_changes())

        # Set without any changes
        remote_files = [SystemFile("a", 0, False), SystemFile("b", 0, False)]
        self.model_builder.set_remote_files(remote_files)
        self.assertFalse(self.model_builder.has_changes())
        model = self.model_builder.build_model()
        self.assertEqual({"a", "b", "c", "d"}, model.get_file_names())

        # Set with changes
        remote_files = [SystemFile("b", 0, False), SystemFile("e", 0, False)]
        self.model_builder.set_remote_files(remote_files)
        self.assertTrue(self.model_builder.has_changes())
        model = self.model_builder.build_model()
        self.assertEqual({"b", "c", "d", "e"}, model.get_file_names())

    def test_rebuild_on_active_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_active_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Invalidates even on same active files
        self.model_builder.set_active_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.assertTrue(self.model_builder.has_changes())
        self.model_builder.build_model()

        # Does not invalidate on empty active files
        self.model_builder.set_active_files([])
        self.assertFalse(self.model_builder.has_changes())

    def test_rebuild_on_local_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_local_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_local_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_local_files([
            SystemFile("a", 10),
            SystemFile("b", 21)
        ])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_remote_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_remote_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_remote_files([
            SystemFile("a", 10),
            SystemFile("b", 20)
        ])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_remote_files([
            SystemFile("a", 10),
            SystemFile("b", 21)
        ])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_lftp_statuses(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        s1 = LftpJobStatus(3, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "flags")
        s1.total_transfer_state = LftpJobStatus.TransferState(100, 200, 50, 10, 50)
        s2 = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", "flags")
        self.model_builder.set_lftp_statuses([s1, s2])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        s1a = LftpJobStatus(3, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "flags")
        s1a.total_transfer_state = LftpJobStatus.TransferState(100, 200, 50, 10, 50)
        s2a = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", "flags")
        self.model_builder.set_lftp_statuses([s1a, s2a])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        s1b = LftpJobStatus(3, LftpJobStatus.Type.MIRROR, LftpJobStatus.State.RUNNING, "a", "flags")
        s1b.total_transfer_state = LftpJobStatus.TransferState(150, 200, 50, 10, 50)
        s2b = LftpJobStatus(3, LftpJobStatus.Type.PGET, LftpJobStatus.State.QUEUED, "b", "flags")
        self.model_builder.set_lftp_statuses([s1b, s2b])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_downloaded_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_downloaded_files({"a", "b"})
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_downloaded_files({"a", "b"})
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_downloaded_files({"a", "c"})
        self.assertTrue(self.model_builder.has_changes())

    def test_clear_does_not_mutate_downloaded_files(self):
        downloaded_files = {"a", "b"}

        self.model_builder.set_downloaded_files(downloaded_files)
        self.model_builder.clear()

        self.assertEqual({"a", "b"}, downloaded_files)

    def test_rebuild_on_in_place_downloaded_file_mutation_after_reset(self):
        downloaded_files = {"a", "b"}

        self.model_builder.set_downloaded_files(downloaded_files)
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        downloaded_files.add("c")
        self.model_builder.set_downloaded_files(downloaded_files)

        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_extract_statuses(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_extract_statuses([
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING),
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING)
        ])
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_extract_statuses([
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING),
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING)
        ])
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_extract_statuses([
            ExtractStatus("a", True, ExtractStatus.State.EXTRACTING),
            ExtractStatus("c", True, ExtractStatus.State.EXTRACTING)
        ])
        self.assertTrue(self.model_builder.has_changes())

    def test_rebuild_on_extracted_files(self):
        self.assertTrue(self.model_builder.has_changes())

        # Initial set
        self.model_builder.set_extracted_files({"a", "b"})
        self.model_builder.build_model()
        self.assertFalse(self.model_builder.has_changes())

        # Does not invalidate on same
        self.model_builder.set_extracted_files({"a", "b"})
        self.assertFalse(self.model_builder.has_changes())

        # Invalidate on different
        self.model_builder.set_extracted_files({"a", "c"})
        self.assertTrue(self.model_builder.has_changes())

    def test_build_model_applies_validation_status(self):
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_validation_statuses([
            ValidateStatus(
                file_id="a",
                state=ModelFile.State.VALIDATING,
                progress=35,
                error=None,
                corrupt_chunks=None
            )
        ])

        model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.VALIDATING, model.get_file("a").state)
        self.assertEqual(35, model.get_file("a").validation_progress)

    def test_build_model_preserves_validation_status_across_rebuilds(self):
        self.model_builder.set_remote_files([SystemFile("a", 100, False)])
        self.model_builder.set_local_files([SystemFile("a", 100, False)])
        self.model_builder.set_validation_statuses([
            ValidateStatus(
                file_id="a",
                state=ModelFile.State.VALIDATED,
                progress=100,
                error=None,
                corrupt_chunks=None
            )
        ])

        first_model = self.model_builder.build_model()
        self.assertEqual(ModelFile.State.VALIDATED, first_model.get_file("a").state)

        updated_local = SystemFile("a", 100, False)
        self.model_builder.set_local_files([updated_local])
        rebuilt_model = self.model_builder.build_model()

        self.assertEqual(ModelFile.State.VALIDATED, rebuilt_model.get_file("a").state)
        self.assertEqual(100, rebuilt_model.get_file("a").validation_progress)

    def test_build_duplicate_root_names_by_path_pair(self):
        remote_movies = SystemFile("dup", 10, False)
        remote_movies.path_pair_id = "movies"
        remote_movies.path_pair_name = "Movies"
        remote_tv = SystemFile("dup", 20, False)
        remote_tv.path_pair_id = "tv"
        remote_tv.path_pair_name = "TV"

        self.model_builder.set_remote_files([remote_movies, remote_tv])

        model = self.model_builder.build_model()

        self.assertEqual({"dup"}, model.get_file_names())
        self.assertEqual(
            {
                ModelFile.build_file_id("dup", "movies"),
                ModelFile.build_file_id("dup", "tv"),
            },
            model.get_file_ids()
        )
        with self.assertRaises(ModelError):
            model.get_file("dup")
        self.assertEqual(
            "movies",
            model.get_file(ModelFile.build_file_id("dup", "movies")).path_pair_id
        )
        self.assertEqual(
            "tv",
            model.get_file(ModelFile.build_file_id("dup", "tv")).path_pair_id
        )

    def test_build_children_inherit_path_pair_metadata(self):
        remote_root = SystemFile("dup", 10, True)
        remote_root.path_pair_id = "movies"
        remote_root.path_pair_name = "Movies"
        remote_child = SystemFile("child", 10, False)
        remote_root.add_child(remote_child)

        self.model_builder.set_remote_files([remote_root])

        model = self.model_builder.build_model()
        built_root = model.get_file(ModelFile.build_file_id("dup", "movies"))
        built_child = built_root.get_children()[0]

        self.assertEqual("movies", built_root.path_pair_id)
        self.assertEqual("Movies", built_root.path_pair_name)
        self.assertEqual("movies", built_child.path_pair_id)
        self.assertEqual("Movies", built_child.path_pair_name)
        self.assertEqual(
            ModelFile.build_file_id(os.path.join("dup", "child"), "movies"),
            built_child.file_id
        )
