# Copyright 2026, SeedSync Contributors, All rights reserved.

import logging
import os
import shutil
import stat
import sys
import tempfile
import unittest
import shlex
import time
from unittest.mock import PropertyMock, patch

import pytest

from common import Args, Config, Context, Status, overrides
from common.path_pair import PathPair, PathPairManager
from controller import Controller, ControllerPersist
from tests.utils import TestUtils


pytestmark = pytest.mark.timeout(30)

class TestControllerMultiPath(unittest.TestCase):
    __KEEP_FILES = False

    @overrides(unittest.TestCase)
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_controller_multi_path_")
        TestUtils.chmod_from_to(self.temp_dir, tempfile.gettempdir(), 0o775)

        self.remote_movies = os.path.join(self.temp_dir, "remote_movies")
        self.remote_tv = os.path.join(self.temp_dir, "remote_tv")
        self.local_movies = os.path.join(self.temp_dir, "local_movies")
        self.local_tv = os.path.join(self.temp_dir, "local_tv")
        self.work_dir = os.path.join(self.temp_dir, "work")
        for path in (
            self.remote_movies,
            self.remote_tv,
            self.local_movies,
            self.local_tv,
            self.work_dir,
        ):
            os.mkdir(path)

        self.duplicate_name = "dup"
        self.movies_pair_id = "movies"
        self.tv_pair_id = "tv"

        self._write_file(self.remote_movies, self.duplicate_name, 1024)
        self._write_file(self.remote_tv, self.duplicate_name, 2048)
        self._allow_group_access(self.remote_movies)
        self._allow_group_access(self.remote_tv)

        local_script_path = os.path.abspath(
            os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "scan_fs.py")
        )
        self.local_scanfs_dir = os.path.join(self.temp_dir, "scanfs_local")
        self.remote_scanfs_dir = os.path.join(self.temp_dir, "scanfs_remote")
        os.makedirs(self.local_scanfs_dir, exist_ok=True)
        os.makedirs(self.remote_scanfs_dir, exist_ok=True)
        os.chmod(self.remote_scanfs_dir, 0o775)
        self.local_scanfs_path = os.path.join(self.local_scanfs_dir, "scanfs")
        with open(self.local_scanfs_path, "w") as handle:
            handle.write("#!/bin/sh\n")
            handle.write("/usr/bin/env python3 {} \"$@\"".format(shlex.quote(local_script_path)))
        os.chmod(self.local_scanfs_path, 0o775)

        ctx_args = Args()
        ctx_args.local_path_to_scanfs = self.local_scanfs_path

        config_dict = {
            "General": {
                "debug": "True",
                "verbose": "True",
            },
            "Lftp": {
                "remote_address": "localhost",
                "remote_username": "seedsynctest",
                "remote_password": "seedsyncpass",
                "remote_port": 22,
                "remote_path": self.remote_movies,
                "local_path": self.local_movies,
                "remote_path_to_scan_script": self.remote_scanfs_dir,
                "use_ssh_key": "True",
                "num_max_parallel_downloads": "1",
                "num_max_parallel_files_per_download": "3",
                "num_max_connections_per_root_file": "4",
                "num_max_connections_per_dir_file": "4",
                "num_max_total_connections": "12",
                "use_temp_file": "False",
                "rate_limit": "0",
                "net_socket_buffer": "512K",
            },
            "Controller": {
                "interval_ms_remote_scan": "100",
                "interval_ms_local_scan": "100",
                "interval_ms_downloading_scan": "100",
                "extract_path": "/unused/path",
                "use_local_path_as_extract_path": True,
            },
            "Web": {
                "port": "8800",
            },
            "AutoQueue": {
                "enabled": "False",
                "patterns_only": "True",
                "auto_extract": "False",
            },
        }

        self.logger = logging.getLogger("{}.{}".format(self.__class__.__name__, id(self)))
        self.logger_handler = logging.StreamHandler(sys.stdout)
        self.logger_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
        self.logger.addHandler(self.logger_handler)
        self.logger.setLevel(logging.DEBUG)

        path_pair_manager = PathPairManager(self.temp_dir)
        path_pair_manager.load()
        path_pair_manager.collection.add_pair(
            PathPair(
                id=self.movies_pair_id,
                name="Movies",
                remote_path=self.remote_movies,
                local_path=self.local_movies,
                enabled=True,
                auto_queue=False,
            )
        )
        path_pair_manager.collection.add_pair(
            PathPair(
                id=self.tv_pair_id,
                name="TV",
                remote_path=self.remote_tv,
                local_path=self.local_tv,
                enabled=True,
                auto_queue=False,
            )
        )

        self.context = Context(
            logger=self.logger,
            web_access_logger=self.logger,
            config=Config.from_dict(config_dict),
            args=ctx_args,
            status=Status(),
            path_pair_manager=path_pair_manager,
        )
        self.controller_persist = ControllerPersist()
        self.controller = None

        remote_timestamp_patch = patch("model.file.ModelFile.remote_modified_timestamp", new_callable=PropertyMock)
        self.addCleanup(remote_timestamp_patch.stop)
        remote_timestamp_patch.start().return_value = None
        local_timestamp_patch = patch("model.file.ModelFile.local_modified_timestamp", new_callable=PropertyMock)
        self.addCleanup(local_timestamp_patch.stop)
        local_timestamp_patch.start().return_value = None

    @overrides(unittest.TestCase)
    def tearDown(self):
        if self.controller:
            self.controller.exit()
        self.logger.removeHandler(self.logger_handler)
        if not TestControllerMultiPath.__KEEP_FILES:
            shutil.rmtree(self.temp_dir)

    @staticmethod
    def _write_file(parent_dir: str, filename: str, size: int):
        with open(os.path.join(parent_dir, filename), "wb") as handle:
            handle.write(bytearray([0xFF] * size))

    @staticmethod
    def _allow_group_access(dir_path: str):
        st_mode = os.stat(dir_path).st_mode
        os.chmod(dir_path, st_mode | stat.S_IWGRP)
        for root, dirs, files in os.walk(dir_path):
            for name in dirs:
                child_path = os.path.join(root, name)
                os.chmod(child_path, os.stat(child_path).st_mode | stat.S_IWGRP)
            for name in files:
                child_path = os.path.join(root, name)
                os.chmod(child_path, os.stat(child_path).st_mode | stat.S_IWGRP)

    def _start_controller(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()

    def _process_until(self, predicate, message: str):
        for _ in range(400):
            self.controller.process()
            if predicate():
                return
            time.sleep(0.05)
        self.fail(message)

    def _wait_for_duplicate_roots(self):
        for _ in range(400):
            self.controller.process()
            if len([
                file for file in self.controller.get_model_files()
                if file.name == self.duplicate_name
            ]) == 2:
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for duplicate multi-path roots to scan")

    def _get_model_file_by_pair(self, path_pair_id: str):
        for model_file in self.controller.get_model_files():
            if model_file.name == self.duplicate_name and model_file.path_pair_id == path_pair_id:
                return model_file
        return None

    def test_scan_keeps_duplicate_top_level_names_per_path_pair(self):
        self._start_controller()
        self._wait_for_duplicate_roots()

        duplicate_files = [
            file for file in self.controller.get_model_files()
            if file.name == self.duplicate_name
        ]

        self.assertEqual(2, len(duplicate_files))
        self.assertEqual(
            {self.movies_pair_id, self.tv_pair_id},
            {file.path_pair_id for file in duplicate_files}
        )
        self.assertEqual(
            {"Movies", "TV"},
            {file.path_pair_name for file in duplicate_files}
        )
        self.assertEqual(
            {file.file_id for file in duplicate_files},
            {
                "[\"movies\",\"dup\"]",
                "[\"tv\",\"dup\"]",
            }
        )

    def test_queue_and_delete_commands_use_matching_path_pair_roots(self):
        self._start_controller()
        self._wait_for_duplicate_roots()

        tv_file = self._get_model_file_by_pair(self.tv_pair_id)
        movies_file = self._get_model_file_by_pair(self.movies_pair_id)
        self.assertIsNotNone(tv_file)
        self.assertIsNotNone(movies_file)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, tv_file.file_id))
        self._process_until(
            lambda: os.path.exists(os.path.join(self.local_tv, self.duplicate_name)),
            "Timed out waiting for tv duplicate to download",
        )
        self.assertEqual(2048, os.path.getsize(os.path.join(self.local_tv, self.duplicate_name)))
        self.assertFalse(os.path.exists(os.path.join(self.local_movies, self.duplicate_name)))

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, movies_file.file_id))
        self._process_until(
            lambda: os.path.exists(os.path.join(self.local_movies, self.duplicate_name)),
            "Timed out waiting for movies duplicate to download",
        )
        self.assertEqual(1024, os.path.getsize(os.path.join(self.local_movies, self.duplicate_name)))

        self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_LOCAL, tv_file.file_id))
        self._process_until(
            lambda: not os.path.exists(os.path.join(self.local_tv, self.duplicate_name)),
            "Timed out waiting for tv local duplicate to delete",
        )
        self.assertTrue(os.path.exists(os.path.join(self.local_movies, self.duplicate_name)))

        self.controller.queue_command(Controller.Command(Controller.Command.Action.DELETE_REMOTE, tv_file.file_id))
        self._process_until(
            lambda: not os.path.exists(os.path.join(self.remote_tv, self.duplicate_name)),
            "Timed out waiting for tv remote duplicate to delete",
        )
        self.assertTrue(os.path.exists(os.path.join(self.remote_movies, self.duplicate_name)))

    def test_refresh_path_pairs_picks_up_newly_enabled_pair_without_restart(self):
        self.context.path_pair_manager.update_pair(
            PathPair(
                id=self.tv_pair_id,
                name="TV",
                remote_path=self.remote_tv,
                local_path=self.local_tv,
                enabled=False,
                auto_queue=False,
            )
        )

        self._start_controller()
        self._process_until(
            lambda: len([
                file for file in self.controller.get_model_files()
                if file.name == self.duplicate_name
            ]) == 1,
            "Timed out waiting for single enabled path pair to scan",
        )
        self.assertIsNone(self._get_model_file_by_pair(self.tv_pair_id))

        self.context.path_pair_manager.update_pair(
            PathPair(
                id=self.tv_pair_id,
                name="TV",
                remote_path=self.remote_tv,
                local_path=self.local_tv,
                enabled=True,
                auto_queue=False,
            )
        )

        self.controller.refresh_path_pairs()
        self._wait_for_duplicate_roots()

        self.assertIsNotNone(self._get_model_file_by_pair(self.movies_pair_id))
        self.assertIsNotNone(self._get_model_file_by_pair(self.tv_pair_id))


if __name__ == "__main__":
    unittest.main()
