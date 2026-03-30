# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import os
import tempfile
import shutil
import shlex
import time
from filecmp import dircmp, cmp
import logging
import sys
import zipfile
import subprocess
from datetime import datetime
import stat

import pytest

from tests.utils import TestUtils
from common import overrides, Context, Config, Args, AppError, Localization, Status
from controller import Controller, ControllerPersist
from model import ModelFile, IModelListener

HAS_RAR = shutil.which("rar") is not None


pytestmark = pytest.mark.timeout(20)

class DummyListener(IModelListener):
    @overrides(IModelListener)
    def file_added(self, file: ModelFile):
        pass

    @overrides(IModelListener)
    def file_updated(self, old_file: ModelFile, new_file: ModelFile):
        pass

    @overrides(IModelListener)
    def file_removed(self, file: ModelFile):
        pass


class DummyCommandCallback(Controller.Command.ICallback):
    @overrides(Controller.Command.ICallback)
    def on_failure(self, error: str):
        pass

    @overrides(Controller.Command.ICallback)
    def on_success(self):
        pass


# noinspection SpellCheckingInspection
class TestController(unittest.TestCase):
    __KEEP_FILES = False  # for debugging

    maxDiff = None
    temp_dir = None
    work_dir = None

    @staticmethod
    def my_mkdir(*args):
        os.mkdir(os.path.join(TestController.temp_dir, *args))

    @staticmethod
    def my_touch(size, *args):
        path = os.path.join(TestController.temp_dir, *args)
        with open(path, 'wb') as f:
            f.write(bytearray([0xff] * size))

    @staticmethod
    def my_sparse_touch(size, *args):
        path = os.path.join(TestController.temp_dir, *args)
        with open(path, 'wb') as f:
            f.seek(size - 1)
            f.write(b"\0")

    @staticmethod
    def create_archive(*args):
        """
        Creates a archive of a text file containing name of archive
        The text file is named "<archive.ext>.txt"
        Returns archive file size
        """
        path = os.path.join(TestController.temp_dir, *args)
        archive_name = os.path.basename(path)
        temp_file_path = os.path.join(TestController.work_dir, archive_name+".txt")
        with open(temp_file_path, "w") as f:
            f.write(os.path.basename(path))

        ext = os.path.splitext(os.path.basename(path))[1]
        ext = ext[1:]
        if ext == "zip":
            zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
            zf.write(temp_file_path, os.path.basename(temp_file_path))
            zf.close()
        elif ext == "rar":
            if not HAS_RAR:
                raise FileNotFoundError("rar executable not available")
            fnull = open(os.devnull, 'w')
            subprocess.Popen(
                [
                    "rar",
                    "a",
                    "-ep",
                    path,
                    temp_file_path
                ],
                stdout=fnull
            ).communicate()
        else:
            raise ValueError("Unsupported archive format: {}".format(os.path.basename(path)))
        return os.path.getsize(path)

    @staticmethod
    def _has_file_updated_state(call_args_list, file_name, state):
        for call in call_args_list:
            new_file = call[0][1]
            if new_file.name == file_name and new_file.state == state:
                return True
        return False

    @overrides(unittest.TestCase)
    def setUp(self):
        # Create a temp directory
        TestController.temp_dir = tempfile.mkdtemp(prefix="test_controller")

        # Allow group access for the seedsynctest account
        TestUtils.chmod_from_to(self.temp_dir, tempfile.gettempdir(), 0o775)

        # Create a work directory for temp files
        TestController.work_dir = os.path.join(TestController.temp_dir, "work")
        os.mkdir(TestController.work_dir)

        # Create a bunch of files and directories
        # remote
        #   ra [dir]
        #     raa [file, 1*1024 bytes]
        #     rab [dir]
        #       raba [file, 5*1024 bytes]
        #       rabb [file, 2*1024 bytes]
        #   rb [dir]
        #     rba [file, 4*1024 bytes]
        #     rbb [file, 5*1024 bytes]
        #   rc [file, 10*1024 bytes]
        # local
        #   la [dir]
        #      laa [file, 1*1024 bytes]
        #      lab [file, 1*1024 bytes]
        #   lb [file, 2*1024 bytes]
        TestController.my_mkdir("remote")
        TestController.my_mkdir("remote", "ra")
        TestController.my_touch(1*1024, "remote", "ra", "raa")
        TestController.my_mkdir("remote", "ra", "rab")
        TestController.my_touch(5*1024, "remote", "ra", "rab", "raba")
        TestController.my_touch(2*1024, "remote", "ra", "rab", "rabb")
        TestController.my_mkdir("remote", "rb")
        TestController.my_touch(4*1024, "remote", "rb", "rba")
        TestController.my_touch(5*1024, "remote", "rb", "rbb")
        TestController.my_touch(10*1024, "remote", "rc")
        TestController.my_mkdir("local")
        TestController.my_mkdir("local", "la")
        TestController.my_touch(1*1024, "local", "la", "laa")
        TestController.my_touch(1*1024, "local", "la", "lab")
        TestController.my_touch(2*1024, "local", "lb")

        # Also create some archives
        # Store the true archive file sizes in a dict
        # remote
        #   rd [dir]
        #     rd.zip [file]
        #   re.rar [file]
        #   rf [dir]
        #     rfa [dir]
        #       rfa.zip [file]
        #     rfb [dir]
        #       rfb.zip [file]
        # local
        #   lc [dir]
        #     lca.rar [file]
        #     lcb.zip [file]
        self.archive_sizes = {}
        TestController.my_mkdir("remote", "rd")
        self.archive_sizes["rd.zip"] = TestController.create_archive("remote", "rd", "rd.zip")
        if HAS_RAR:
            self.archive_sizes["re.rar"] = TestController.create_archive("remote", "re.rar")
        TestController.my_mkdir("remote", "rf")
        TestController.my_mkdir("remote", "rf", "rfa")
        self.archive_sizes["rfa.zip"] = TestController.create_archive("remote", "rf", "rfa", "rfa.zip")
        TestController.my_mkdir("remote", "rf", "rfb")
        self.archive_sizes["rfb.zip"] = TestController.create_archive("remote", "rf", "rfb", "rfb.zip")
        TestController.my_mkdir("local", "lc")
        if HAS_RAR:
            self.archive_sizes["lca.rar"] = TestController.create_archive("local", "lc", "lca.rar")
        self.archive_sizes["lcb.zip"] = TestController.create_archive("local", "lc", "lcb.zip")

        # Allow group access to remote files for seedsynctest account
        # This is necessary for seedsynctest can do remote-delete commands
        # We are basically doing a chmod g+w on all of remote/ directory
        remote_dir = os.path.join(self.temp_dir, "remote")
        st = os.stat(remote_dir)
        os.chmod(remote_dir, st.st_mode | stat.S_IWGRP)
        for root, dirs, files in os.walk(remote_dir):
            for momo in dirs:
                path = os.path.join(root, momo)
                st = os.stat(path)
                os.chmod(path, st.st_mode | stat.S_IWGRP)
            for momo in files:
                path = os.path.join(root, momo)
                st = os.stat(path)
                os.chmod(path, st.st_mode | stat.S_IWGRP)

        # Helper object to store the intial state
        f_ra = ModelFile("ra", True)
        f_ra.remote_size = 8*1024
        f_raa = ModelFile("raa", False)
        f_raa.remote_size = 1*1024
        f_ra.add_child(f_raa)
        f_rab = ModelFile("rab", True)
        f_rab.remote_size = 7*1024
        f_ra.add_child(f_rab)
        f_raba = ModelFile("raba", False)
        f_raba.remote_size = 5*1024
        f_rab.add_child(f_raba)
        f_rabb = ModelFile("rabb", False)
        f_rabb.remote_size = 2*1024
        f_rab.add_child(f_rabb)
        f_rb = ModelFile("rb", True)
        f_rb.remote_size = 9*1024
        f_rba = ModelFile("rba", False)
        f_rba.remote_size = 4*1024
        f_rb.add_child(f_rba)
        f_rbb = ModelFile("rbb", False)
        f_rbb.remote_size = 5*1024
        f_rb.add_child(f_rbb)
        f_rc = ModelFile("rc", False)
        f_rc.remote_size = 10*1024

        f_rd = ModelFile("rd", True)
        f_rd.remote_size = self.archive_sizes["rd.zip"]
        f_rd.is_extractable = True
        f_rdx = ModelFile("rd.zip", False)
        f_rdx.remote_size = self.archive_sizes["rd.zip"]
        f_rdx.is_extractable = True
        f_rd.add_child(f_rdx)
        f_rf = ModelFile("rf", True)
        f_rf.remote_size = self.archive_sizes["rfa.zip"] + self.archive_sizes["rfb.zip"]
        f_rf.is_extractable = True
        f_rfa = ModelFile("rfa", True)
        f_rfa.remote_size = self.archive_sizes["rfa.zip"]
        f_rfa.is_extractable = True
        f_rfax = ModelFile("rfa.zip", False)
        f_rfax.remote_size = self.archive_sizes["rfa.zip"]
        f_rfax.is_extractable = True
        f_rfa.add_child(f_rfax)
        f_rf.add_child(f_rfa)
        f_rfb = ModelFile("rfb", True)
        f_rfb.remote_size = self.archive_sizes["rfb.zip"]
        f_rfb.is_extractable = True
        f_rf.add_child(f_rfb)
        f_rfbx = ModelFile("rfb.zip", False)
        f_rfbx.remote_size = self.archive_sizes["rfb.zip"]
        f_rfbx.is_extractable = True
        f_rfb.add_child(f_rfbx)

        f_la = ModelFile("la", True)
        f_la.local_size = 2*1024
        f_laa = ModelFile("laa", False)
        f_laa.local_size = 1*1024
        f_la.add_child(f_laa)
        f_lab = ModelFile("lab", False)
        f_lab.local_size = 1*1024
        f_la.add_child(f_lab)
        f_lb = ModelFile("lb", False)
        f_lb.local_size = 2*1024

        f_lc = ModelFile("lc", True)
        f_lc.local_size = self.archive_sizes["lcb.zip"]
        f_lc.is_extractable = True
        f_lcb = ModelFile("lcb.zip", False)
        f_lcb.local_size = self.archive_sizes["lcb.zip"]
        f_lcb.is_extractable = True
        f_lc.add_child(f_lcb)

        if HAS_RAR:
            f_re = ModelFile("re.rar", False)
            f_re.remote_size = self.archive_sizes["re.rar"]
            f_re.is_extractable = True

            f_lca = ModelFile("lca.rar", False)
            f_lca.local_size = self.archive_sizes["lca.rar"]
            f_lca.is_extractable = True
            f_lc.local_size += self.archive_sizes["lca.rar"]
            f_lc.add_child(f_lca)

        initial_files = [
            f_ra, f_rb, f_rc, f_rd, f_rf,
            f_la, f_lb, f_lc
        ]
        if HAS_RAR:
            initial_files.append(f_re)
        self.initial_state = {f.name: f for f in initial_files}

        # We need to overwrite the timestamp properties since it's too tedious to make
        # them match manually for all the model files
        pm = patch("model.file.ModelFile.remote_modified_timestamp", new_callable=PropertyMock)
        self.addCleanup(pm.stop)
        pm_cls = pm.start()
        pm_cls.return_value = None
        pm = patch("model.file.ModelFile.remote_created_timestamp", new_callable=PropertyMock)
        self.addCleanup(pm.stop)
        pm_cls = pm.start()
        pm_cls.return_value = None
        pm = patch("model.file.ModelFile.local_created_timestamp", new_callable=PropertyMock)
        self.addCleanup(pm.stop)
        pm_cls = pm.start()
        pm_cls.return_value = None
        pm = patch("model.file.ModelFile.local_modified_timestamp", new_callable=PropertyMock)
        self.addCleanup(pm.stop)
        pm_cls = pm.start()
        pm_cls.return_value = None

        # config file
        # Note: seedsynctest account must be set up. See DeveloperReadme.md for details

        # We also need to create an executable that the controller can install on remote
        # Since we don't have a packaged scanfs executable here, we simply
        # create an sh script that points to the python script
        # Note: use a remote-safe interpreter entry point so the remote user does not
        # depend on a private local Poetry path.
        current_dir_path = os.path.dirname(os.path.realpath(__file__))
        local_script_path = os.path.abspath(os.path.join(current_dir_path, "..", "..", "..", "scan_fs.py"))
        local_exe_dir = os.path.join(TestController.temp_dir, "scanfs_local")
        remote_exe_dir = os.path.join(TestController.temp_dir, "scanfs_remote")
        os.makedirs(local_exe_dir, exist_ok=True)
        os.makedirs(remote_exe_dir, exist_ok=True)
        # Allow group access for the seedsynctest account
        os.chmod(remote_exe_dir, 0o775)
        local_exe_path = os.path.join(local_exe_dir, "scanfs")
        remote_exe_path = remote_exe_dir
        with open(local_exe_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write("/usr/bin/env python3 {} \"$@\"".format(shlex.quote(local_script_path)))
        os.chmod(local_exe_path, 0o775)
        ctx_args = Args()
        ctx_args.local_path_to_scanfs = local_exe_path

        config_dict = {
            "General": {
                "debug": "True",
                "verbose": "True"
            },
            "Lftp": {
                "remote_address": "localhost",
                "remote_username": "seedsynctest",
                "remote_password": "seedsyncpass",
                "rate_limit": "0",
                "remote_port": 22,
                "remote_path": os.path.join(self.temp_dir, "remote"),
                "local_path": os.path.join(self.temp_dir, "local"),
                "remote_path_to_scan_script": remote_exe_path,
                "use_ssh_key": "True",
                "num_max_parallel_downloads": "1",
                "num_max_parallel_files_per_download": "3",
                "num_max_connections_per_root_file": "4",
                "num_max_connections_per_dir_file": "4",
                "num_max_total_connections": "12",
                "use_temp_file": "False"
            },
            "Controller": {
                "interval_ms_remote_scan": "100",
                "interval_ms_local_scan": "100",
                "interval_ms_downloading_scan": "100",
                "extract_path": "/unused/path",
                "use_local_path_as_extract_path": True
            },
            "Web": {
                "port": "8800",
            },
            "AutoQueue": {
                "enabled": "True",
                "patterns_only": "True",
                "auto_extract": "True"
            }
        }

        logger = logging.getLogger(TestController.__name__)
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)
        self.context = Context(logger=logger,
                               web_access_logger=logger,
                               config=Config.from_dict(config_dict),
                               args=ctx_args,
                               status=Status())
        self.controller_persist = ControllerPersist()
        self.controller = None

    @overrides(unittest.TestCase)
    def tearDown(self):
        if self.controller:
            self.controller.exit()

        # Cleanup
        if not TestController.__KEEP_FILES:
            shutil.rmtree(self.temp_dir)

    # noinspection PyMethodMayBeStatic
    def __wait_for_initial_model(self):
        while len(self.controller.get_model_files()) < 5:
            self.controller.process()

    def __process_until(self, predicate, message, max_iterations=2000):
        for _ in range(max_iterations):
            self.controller.process()
            if predicate():
                return
        self.fail(message)

    def __find_model_file(self, name):
        return next((file for file in self.controller.get_model_files() if file.name == name), None)

    def __get_model_file(self, name):
        file = self.__find_model_file(name)
        self.assertIsNotNone(file, "File '{}' not found in model".format(name))
        return file

    def __wait_for_model_file(self, name, predicate, message, max_iterations=2000):
        match = {}

        def _predicate():
            file = self.__find_model_file(name)
            if file is None or not predicate(file):
                return False
            match["file"] = file
            return True

        self.__process_until(_predicate, message, max_iterations=max_iterations)
        return match["file"]

    def test_bad_config_doesnot_raise_ctor_exception(self):
        self.context.config.lftp.remote_address = "<bad>"
        self.context.config.lftp.remote_username = "<bad>"
        self.context.config.lftp.remote_path = "<bad>"
        self.context.config.lftp.local_path = "<bad>"
        self.context.config.lftp.remote_path_to_scan_script = "<bad>"
        # noinspection PyBroadException
        try:
            self.controller = Controller(self.context, self.controller_persist)
        except Exception:
            self.fail("Controller ctor raised exception unexpectedly")

    def test_bad_config_remote_address_raises_exception(self):
        self.context.config.lftp.remote_address = "<bad>"
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnusedLocal
        with self.assertRaises(AppError) as error:
            while True:
                self.controller.process()
        # noinspection PyUnreachableCode
        error_str = str(error.exception)
        self.assertTrue(
            "Bad hostname" in error_str or
            "invalid" in error_str.lower() or
            "<bad>" in error_str,
            "Unexpected error message: %s" % error_str
        )

    def test_bad_config_remote_username_raises_exception(self):
        self.context.config.lftp.remote_username = "<bad>"
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnusedLocal
        with self.assertRaises(AppError) as error:
            while True:
                self.controller.process()
        # noinspection PyUnreachableCode
        error_str = str(error.exception)
        self.assertTrue(
            "Permission denied" in error_str or
            "invalid" in error_str.lower() or
            "<bad>" in error_str,
            "Unexpected error message: %s" % error_str
        )

    def test_bad_config_remote_path_raises_exception(self):
        self.context.config.lftp.remote_path = "<bad>"
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnusedLocal
        with self.assertRaises(AppError) as error:
            while True:
                self.controller.process()
        # noinspection PyUnreachableCode
        self.assertEqual(
            Localization.Error.REMOTE_SERVER_SCAN.format("SystemScannerError: Path does not exist: <bad>"),
            str(error.exception)
        )

    def test_bad_config_local_path_raises_exception(self):
        self.context.config.lftp.local_path = "<bad>"
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnusedLocal
        with self.assertRaises(AppError) as error:
            while True:
                self.controller.process()
        # noinspection PyUnreachableCode
        self.assertEqual(Localization.Error.LOCAL_SERVER_SCAN, str(error.exception))

    def test_bad_config_remote_path_to_scan_script_raises_exception(self):
        self.context.config.lftp.remote_path_to_scan_script = "<bad>"
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnusedLocal
        with self.assertRaises(AppError) as error:
            while True:
                self.controller.process()
        # noinspection PyUnreachableCode
        error_str = str(error.exception)
        self.assertTrue(
            "No such file or directory" in error_str or
            "Permission denied" in error_str or
            "<bad>" in error_str,
            "Unexpected error message: %s" % error_str
        )

    def test_bad_remote_password_raises_exception(self):
        self.context.config.lftp.remote_password = "bad password"
        self.context.config.lftp.use_ssh_key = False
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnusedLocal
        with self.assertRaises(AppError) as error:
            while True:
                self.controller.process()
        # noinspection PyUnreachableCode
        self.assertEqual(
            Localization.Error.REMOTE_SERVER_INSTALL.format("Incorrect password"),
            str(error.exception)
        )

    def test_initial_model(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        model_files = self.controller.get_model_files()
        self.assertEqual(len(self.initial_state.keys()), len(model_files))
        files_dict = {f.name: f for f in model_files}
        self.assertEqual(self.initial_state.keys(), files_dict.keys())
        for filename in self.initial_state.keys():
            # Note: put items in a list for a better diff output
            self.assertEqual([self.initial_state[filename]], [files_dict[filename]],
                             "Mismatch in file: {}".format(filename))

    def test_local_file_added(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Add a local file
        TestController.my_touch(1515, "local", "lnew")

        # Process until discovered
        while True:
            self.controller.process()
            call = listener.file_added.call_args
            if call:
                new_file = call[0][0]
                self.assertEqual("lnew", new_file.name)
                break

        # Verify
        self.controller.process()
        lnew = ModelFile("lnew", False)
        lnew.local_size = 1515
        listener.file_added.assert_called_once_with(lnew)
        listener.file_updated.assert_not_called()
        listener.file_removed.assert_not_called()

    def test_local_file_updated(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Update a local file
        TestController.my_touch(1717, "local", "lb")

        # Process until discovered
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("lb", new_file.name)
                break

        # Verify
        self.controller.process()
        lb_old = ModelFile("lb", False)
        lb_old.local_size = 2*1024
        lb_new = ModelFile("lb", False)
        lb_new.local_size = 1717
        listener.file_updated.assert_called_once_with(lb_old, lb_new)
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()

    def test_local_file_removed(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Remove the local file
        os.remove(os.path.join(TestController.temp_dir, "local", "lb"))

        # Process until discovered
        while True:
            self.controller.process()
            call = listener.file_removed.call_args
            if call:
                new_file = call[0][0]
                self.assertEqual("lb", new_file.name)
                break

        # Verify
        self.controller.process()
        lb = ModelFile("lb", False)
        lb.local_size = 2*1024
        listener.file_removed.assert_called_once_with(lb)
        listener.file_added.assert_not_called()
        listener.file_updated.assert_not_called()

    def test_remote_file_added(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Add a local file
        TestController.my_touch(1515, "remote", "rnew")

        # Verify
        while listener.file_added.call_count < 1:
            self.controller.process()

        rnew = ModelFile("rnew", False)
        rnew.remote_size = 1515
        listener.file_added.assert_called_once_with(rnew)
        listener.file_updated.assert_not_called()
        listener.file_removed.assert_not_called()

    def test_remote_file_updated(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Update a local file
        TestController.my_touch(1717, "remote", "rc")

        # Verify
        while listener.file_updated.call_count < 1:
            self.controller.process()

        rc_old = ModelFile("rc", False)
        rc_old.remote_size = 10*1024
        rc_new = ModelFile("rc", False)
        rc_new.remote_size = 1717
        listener.file_updated.assert_called_once_with(rc_old, rc_new)
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()

    def test_remote_file_removed(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Remove the local file
        os.remove(os.path.join(TestController.temp_dir, "remote", "rc"))

        # Verify
        while listener.file_removed.call_count < 1:
            self.controller.process()

        rc = ModelFile("rc", False)
        rc.remote_size = 10*1024
        listener.file_removed.assert_called_once_with(rc)
        listener.file_added.assert_not_called()
        listener.file_updated.assert_not_called()

    def test_command_queue_directory(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "ra")
        command.add_callback(callback)
        self.controller.queue_command(command)
        final_target = os.path.join(TestController.temp_dir, "local", "ra")
        staging_target = os.path.join(TestController.temp_dir, "local", "incomplete", "ra")

        # Process until the staged directory has been promoted to the final local root
        downloaded_file = self.__wait_for_model_file(
            "ra",
            lambda file: file.state == ModelFile.State.DOWNLOADED and os.path.exists(final_target),
            "Timed out waiting for ra directory queue to finish",
            max_iterations=4000,
        )

        for _ in range(20):
            self.controller.process()

        # Verify
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        dcmp = dircmp(os.path.join(TestController.temp_dir, "remote", "ra"), final_target)
        self.assertFalse(dcmp.left_only)
        self.assertFalse(dcmp.right_only)
        self.assertFalse(dcmp.diff_files)
        self.assertEqual(ModelFile.State.DOWNLOADED, downloaded_file.state)
        self.assertTrue(os.path.exists(final_target))
        self.assertFalse(os.path.exists(staging_target))

    def test_command_queue_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rc")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until done
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rc", new_file.name)
                if new_file.local_size == 10*1024:
                    break

        # Verify
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        fcmp = cmp(os.path.join(TestController.temp_dir, "remote", "rc"),
                   os.path.join(TestController.temp_dir, "local", "rc"))
        self.assertTrue(fcmp)

    def test_command_queue_invalid(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "invaliddir")
        command.add_callback(callback)
        self.controller.queue_command(command)

        # Process until done
        while callback.on_failure.call_count < 1:
            self.controller.process()

        # Verify
        listener.file_added.assert_not_called()
        listener.file_updated.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'invaliddir' not found", error)

    def test_command_queue_local_directory(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "la")
        command.add_callback(callback)
        self.controller.queue_command(command)

        # Process until done
        while callback.on_failure.call_count < 1:
            self.controller.process()

        listener.file_added.assert_not_called()
        listener.file_updated.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'la' does not exist remotely", error)

    def test_command_queue_local_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "lb")
        command.add_callback(callback)
        self.controller.queue_command(command)

        # Process until done
        while callback.on_failure.call_count < 1:
            self.controller.process()

        listener.file_added.assert_not_called()
        listener.file_updated.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'lb' does not exist remotely", error)

    def test_command_stop_directory(self):
        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "ra")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download starts
        for _ in range(300):
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("ra", new_file.name)
                if new_file.local_size and new_file.local_size > 0:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for ra download to start")

        # Now stop the download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, "ra"))

        # Process until download stops
        for _ in range(300):
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("ra", new_file.name)
                if new_file.state == ModelFile.State.DEFAULT:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for ra download to stop")

        # Verify
        call = listener.file_updated.call_args
        new_file = call[0][1]
        self.assertEqual("ra", new_file.name)
        self.assertEqual(ModelFile.State.DEFAULT, new_file.state)
        self.assertLess(new_file.local_size, new_file.remote_size)

        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_command_stop_file(self):
        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rc")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Seed the pget sidecar so this test can observe the gated stoppable state deterministically.
        local_incomplete_dir = os.path.join(TestController.temp_dir, "local", "incomplete")
        os.makedirs(local_incomplete_dir, exist_ok=True)
        status_payload = "size=10240\n0.pos=1\n0.limit=10240\n"
        for status_name in ("rc.lftp-pget-status", "rc.lftp.lftp-pget-status"):
            with open(os.path.join(local_incomplete_dir, status_name), "w") as handle:
                handle.write(status_payload)
        self.controller._Controller__local_scan_process.force_scan()
        self.controller._Controller__active_scan_process.force_scan()
        # Process until download starts
        for attempt in range(300):
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rc", new_file.name)
                if new_file.local_size and new_file.local_size > 0 and new_file.is_stoppable:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for rc download to become stoppable")

        # Now stop the download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, "rc"))

        # Process until download stops
        for _ in range(300):
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rc", new_file.name)
                if new_file.state == ModelFile.State.DEFAULT:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for rc download to stop")

        # Verify
        call = listener.file_updated.call_args
        new_file = call[0][1]
        self.assertEqual("rc", new_file.name)
        self.assertEqual(ModelFile.State.DEFAULT, new_file.state)
        self.assertLess(new_file.local_size, new_file.remote_size)

        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_command_stop_default(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Verify that rc is Default
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DEFAULT, files_dict["rc"].state)

        # Now stop the download
        command = Controller.Command(Controller.Command.Action.STOP, "rc")
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller.process()

        # Verify nothing happened
        listener.file_updated.assert_not_called()
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'rc' is not Queued or Downloading", error)

    def test_command_stop_queued(self):
        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue two downloads
        # This one will be Downloading
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "rc"))
        # This one will be Queued
        command = Controller.Command(Controller.Command.Action.QUEUE, "rb")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until rc starts.
        self.__wait_for_model_file(
            "rc",
            lambda file: file.local_size is not None and file.local_size > 0,
            "Timed out waiting for rc download to start",
        )

        # Verify that rb is Queued
        queued_file = self.__wait_for_model_file(
            "rb",
            lambda file: file.state == ModelFile.State.QUEUED,
            "Timed out waiting for rb to become queued",
        )
        self.assertEqual(ModelFile.State.QUEUED, queued_file.state)

        # Now stop the queued
        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, "rb"))

        # Process until queued stops
        self.__wait_for_model_file(
            "rb",
            lambda file: file.state == ModelFile.State.DEFAULT,
            "Timed out waiting for queued file to stop",
        )

        # Verify that rc is Downloading, rb is Default
        files_dict = {f.name: f for f in self.controller.get_model_files()}
        self.assertEqual(ModelFile.State.DOWNLOADING, files_dict["rc"].state)
        self.assertEqual(ModelFile.State.DEFAULT, files_dict["rb"].state)

        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

    def test_command_stop_wrong(self):
        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "ra"))
        # Process until download starts
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("ra", new_file.name)
                if new_file.local_size and new_file.local_size > 0:
                    break

        # Now stop the download with wrong name
        command = Controller.Command(Controller.Command.Action.STOP, "rb")
        command.add_callback(callback)
        self.controller.queue_command(command)

        # Process until done
        while callback.on_failure.call_count < 1:
            self.controller.process()

        # Verify that downloading is still going
        call = listener.file_updated.call_args
        new_file = call[0][1]
        self.assertEqual("ra", new_file.name)
        self.assertEqual(ModelFile.State.DOWNLOADING, new_file.state)

        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'rb' is not Queued or Downloading", error)

    def test_command_stop_invalid(self):
        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "ra"))
        # Process until download starts
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("ra", new_file.name)
                if new_file.local_size and new_file.local_size > 0:
                    break

        # Now stop the download with wrong name
        command = Controller.Command(Controller.Command.Action.STOP, "invalidfile")
        command.add_callback(callback)
        self.controller.queue_command(command)

        # Process until done
        while callback.on_failure.call_count < 1:
            self.controller.process()

        # Verify that downloading is still going
        call = listener.file_updated.call_args
        new_file = call[0][1]
        self.assertEqual("ra", new_file.name)
        self.assertEqual(ModelFile.State.DOWNLOADING, new_file.state)

        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'invalidfile' not found", error)

    @unittest.skipUnless(HAS_RAR, "rar executable not available")
    def test_command_extract_after_downloading_remote_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "re.rar")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("re.rar", new_file.name)
                if new_file.state == ModelFile.State.DOWNLOADED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "re.rar")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        while True:
            self.controller.process()
            if self._has_file_updated_state(
                listener.file_updated.call_args_list,
                "re.rar",
                ModelFile.State.EXTRACTED
            ):
                break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        # Verify
        re_txt_path = os.path.join(TestController.temp_dir, "local", "re", "re.rar.txt")
        # The controller can report extraction complete just before the file
        # write becomes visible on disk, so wait briefly for the artifact.
        for _ in range(100):
            if os.path.isfile(re_txt_path) and os.path.getsize(re_txt_path) > 0:
                break
            self.controller.process()
        else:
            self.fail("Timed out waiting for re.rar.txt to be written")
        with open(re_txt_path, "r") as f:
            self.assertEqual("re.rar", f.read())

    def test_command_extract_after_downloading_remote_directory(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.DOWNLOADED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.EXTRACTED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        # Verify
        rd_txt_path = os.path.join(TestController.temp_dir, "local", "rd", "rd.zip.txt")
        self.assertTrue(os.path.isfile(rd_txt_path))
        with open(rd_txt_path, "r") as f:
            self.assertEqual("rd.zip", f.read())

    def test_command_extract_after_downloading_remote_directory_multilevel(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rf")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rf", new_file.name)
                if new_file.state == ModelFile.State.DOWNLOADED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "rf")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rf", new_file.name)
                if new_file.state == ModelFile.State.EXTRACTED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        # Verify
        rfa_txt_path = os.path.join(TestController.temp_dir, "local", "rf", "rfa", "rfa.zip.txt")
        self.assertTrue(os.path.isfile(rfa_txt_path))
        with open(rfa_txt_path, "r") as f:
            self.assertEqual("rfa.zip", f.read())
        rfb_txt_path = os.path.join(TestController.temp_dir, "local", "rf", "rfb", "rfb.zip.txt")
        self.assertTrue(os.path.isfile(rfb_txt_path))
        with open(rfb_txt_path, "r") as f:
            self.assertEqual("rfb.zip", f.read())

    @unittest.skipUnless(HAS_RAR, "rar executable not available")
    def test_command_extract_local_directory(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "lc")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        # Can't rely on state changes since final state is back to Default
        # Look for presence of extracted files
        lca_txt_path = os.path.join(TestController.temp_dir, "local", "lc", "lca", "lca.rar.txt")
        lcb_txt_path = os.path.join(TestController.temp_dir, "local", "lc", "lcb", "lcb.zip.txt")
        for _ in range(150):
            self.controller.process()
            if os.path.isfile(lca_txt_path) and os.path.isfile(lcb_txt_path) \
                    and os.path.getsize(lca_txt_path) > 0 \
                    and os.path.getsize(lcb_txt_path) > 0:
                break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for extracted files to be written")
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        # Verify
        with open(lca_txt_path, "r") as f:
            self.assertEqual("lca.rar", f.read())
        with open(lcb_txt_path, "r") as f:
            self.assertEqual("lcb.zip", f.read())

    @unittest.skipUnless(HAS_RAR, "rar executable not available")
    def test_command_reextract_after_extracting_remote_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "re.rar")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        for _ in range(300):
            self.controller.process()
            if self._has_file_updated_state(
                listener.file_updated.call_args_list,
                "re.rar",
                ModelFile.State.DOWNLOADED
            ):
                break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for re.rar to download")
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "re.rar")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        re_txt_path = os.path.join(TestController.temp_dir, "local", "re", "re.rar.txt")
        for _ in range(150):
            self.controller.process()
            if self._has_file_updated_state(
                listener.file_updated.call_args_list,
                "re.rar",
                ModelFile.State.EXTRACTED
            ):
                break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for re.rar to extract")
        callback.on_success.assert_called_once_with()
        callback.on_success.reset_mock()
        callback.on_failure.assert_not_called()

        # Verify
        re_txt_path = os.path.join(TestController.temp_dir, "local", "re", "re.rar.txt")
        self.assertTrue(os.path.isfile(re_txt_path))
        with open(re_txt_path, "r") as f:
            self.assertEqual("re.rar", f.read())

        # Delete the extracted file
        os.remove(re_txt_path)
        self.assertFalse(os.path.isfile(re_txt_path))

        # Queue a re-extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "re.rar")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        # Can't rely on state changes since final state is back to Extracted
        # Look for presence of extracted file
        for _ in range(150):
            self.controller.process()
            if os.path.isfile(re_txt_path) and os.path.getsize(re_txt_path) > 0:
                break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for re.rar.txt to be written")

        # Verify again
        self.assertTrue(os.path.isfile(re_txt_path))
        with open(re_txt_path, "r") as f:
            self.assertEqual("re.rar", f.read())

    @unittest.skipUnless(HAS_RAR, "rar executable not available")
    def test_command_extract_remote_only_fails(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Verify that rc is Default
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DEFAULT, files_dict["re.rar"].state)

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "re.rar")
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller.process()

        # Verify nothing happened
        listener.file_updated.assert_not_called()
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 're.rar' does not exist locally", error)

    def test_command_extract_after_downloading_remote_directory_to_separate_path(self):
        # Change the extract path
        extract_path = os.path.join(TestController.temp_dir, "extract")
        os.mkdir(extract_path)
        self.context.config.controller.extract_path = extract_path
        self.context.config.controller.use_local_path_as_extract_path = False
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.DOWNLOADED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.EXTRACTED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        # Verify
        rd_txt_path = os.path.join(extract_path, "rd", "rd.zip.txt")
        self.assertTrue(os.path.isfile(rd_txt_path))
        with open(rd_txt_path, "r") as f:
            self.assertEqual("rd.zip", f.read())

    def test_command_redownload_after_deleting_extracted_file(self):
        """
        File is downloaded, then extracted, then deleted, then redownloaded
        Verify that final state is Downloaded and NOT Extracted
        """
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.DOWNLOADED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Queue an extraction
        command = Controller.Command(Controller.Command.Action.EXTRACT, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until extract complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.EXTRACTED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_success.reset_mock()
        callback.on_failure.assert_not_called()

        # Verify
        re_txt_path = os.path.join(TestController.temp_dir, "local", "rd", "rd.zip.txt")
        self.assertTrue(os.path.isfile(re_txt_path))
        with open(re_txt_path, "r") as f:
            self.assertEqual("rd.zip", f.read())

        # Delete the whole thing
        shutil.rmtree(os.path.join(TestController.temp_dir, "local", "rd"))

        # Process until deleted state
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                if new_file.state == ModelFile.State.DELETED:
                    break

        # Queue the download AGAIN
        command = Controller.Command(Controller.Command.Action.QUEUE, "rd")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download complete
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rd", new_file.name)
                # EXTRACTED is wrong, but we check for that later on
                if new_file.state == ModelFile.State.DOWNLOADED or \
                        new_file.state == ModelFile.State.EXTRACTED:
                    break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        callback.on_success.reset_mock()

        # Verify file is in DOWNLOADED state
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DOWNLOADED, files_dict["rd"].state)

    def test_config_num_max_parallel_downloads(self):
        self.context.config.lftp.num_max_parallel_downloads = 2
        self.controller = Controller(self.context, ControllerPersist())
        self.controller.start()

        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Queue 3 downloads
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "ra"))
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "rb"))
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "rc"))

        # Process until 2 downloads starts
        ra_downloading = False
        rb_downloading = False

        # noinspection PyUnusedLocal
        def updated_side_effect(old_file: ModelFile, new_file: ModelFile):
            nonlocal ra_downloading, rb_downloading
            if new_file.local_size and new_file.local_size > 0:
                if new_file.name == "ra":
                    ra_downloading = True
                elif new_file.name == "rb":
                    rb_downloading = True
            return
        listener.file_updated.side_effect = updated_side_effect
        while True:
            self.controller.process()
            if ra_downloading and rb_downloading:
                break

        # Verify that ra, rb is Downloading, rc is Queued
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DOWNLOADING, files_dict["ra"].state)
        self.assertEqual(ModelFile.State.DOWNLOADING, files_dict["rb"].state)
        self.assertEqual(ModelFile.State.QUEUED, files_dict["rc"].state)

    def test_downloading_scan(self):
        # Test that downloading scan is independent of local scan
        # Set a very large local scan interval and verify that downloading
        # updates are still propagated
        self.context.config.controller.interval_ms_downloading_scan = 200
        self.context.config.controller.interval_ms_local_scan = 10000
        self.controller = Controller(self.context, ControllerPersist())
        self.controller.start()

        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Queue a download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "ra"))

        # Process until the downloads starts
        ra_downloading = False

        # noinspection PyUnusedLocal
        def updated_side_effect(old_file: ModelFile, new_file: ModelFile):
            nonlocal ra_downloading
            if new_file.local_size and new_file.local_size > 0:
                if new_file.name == "ra":
                    ra_downloading = True
            return
        listener.file_updated.side_effect = updated_side_effect
        while True:
            self.controller.process()
            if ra_downloading:
                break

        # Verify that ra is Downloading
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DOWNLOADING, files_dict["ra"].state)

    def test_persist_downloaded(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Verify empty download state
        self.assertEqual(0, len(self.controller_persist.downloaded_file_names))

        # Download rc
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "rc"))

        # Process until the downloads starts
        rc_downloaded = False

        # noinspection PyUnusedLocal
        def updated_side_effect(old_file: ModelFile, new_file: ModelFile):
            nonlocal rc_downloaded
            if new_file.state == ModelFile.State.DOWNLOADED and new_file.name == "rc":
                    rc_downloaded = True
            return
        listener.file_updated.side_effect = updated_side_effect
        while True:
            self.controller.process()
            if rc_downloaded:
                break

        self.assertTrue(rc_downloaded)
        # Verify downloaded state was persisted
        self.assertTrue("rc" in self.controller_persist.downloaded_file_names)

    def test_redownload_deleted_file(self):
        # Test that a previously downloaded then deleted file can be redownloaded
        # We set the downloaded state in controller persist
        self.controller_persist.downloaded_file_names.add("ra")
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()

        # White box hack: limit the rate of lftp so download doesn't finish
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 100

        # wait for initial scan
        self.__wait_for_initial_model()

        # Verify that ra is marked as Deleted
        self.controller.process()
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DELETED, files_dict["ra"].state)

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        # Queue a download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, "ra"))

        # Process until the downloads starts
        ra_downloading = False

        # noinspection PyUnusedLocal
        def updated_side_effect(old_file: ModelFile, new_file: ModelFile):
            nonlocal ra_downloading
            if new_file.local_size and new_file.local_size > 0:
                if new_file.name == "ra":
                    ra_downloading = True
            return
        listener.file_updated.side_effect = updated_side_effect
        while True:
            self.controller.process()
            if ra_downloading:
                break

        # Verify that ra is Downloading
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DOWNLOADING, files_dict["ra"].state)

    def test_command_delete_local_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "local", "lb")
        self.assertTrue(os.path.isfile(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "lb")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until file is removed from model
        while True:
            self.controller.process()
            call = listener.file_removed.call_args
            if call:
                file = call[0][0]
                self.assertEqual("lb", file.name)
                break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        self.assertFalse(os.path.exists(file_path))

    def test_command_delete_local_dir(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "local", "la")
        self.assertTrue(os.path.isdir(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "la")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until file is removed from model
        while True:
            self.controller.process()
            call = listener.file_removed.call_args
            if call:
                file = call[0][0]
                self.assertEqual("la", file.name)
                break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        self.assertFalse(os.path.exists(file_path))

    def test_command_delete_remote_dir(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "remote", "ra")
        self.assertTrue(os.path.isdir(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, "ra")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until file is removed from model
        while True:
            self.controller.process()
            call = listener.file_removed.call_args
            if call:
                file = call[0][0]
                self.assertEqual("ra", file.name)
                break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        self.assertFalse(os.path.exists(file_path))

    def test_command_delete_local_fails_on_remote_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "remote", "ra")
        self.assertTrue(os.path.isdir(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "ra")
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller.process()

        # Verify nothing happened
        listener.file_updated.assert_not_called()
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'ra' does not exist locally", error)

        self.assertTrue(os.path.isdir(file_path))

    def test_command_delete_remote_fails_on_local_file(self):
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "local", "la")
        self.assertTrue(os.path.isdir(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, "la")
        command.add_callback(callback)
        self.controller.queue_command(command)
        self.controller.process()

        # Verify nothing happened
        listener.file_updated.assert_not_called()
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_not_called()
        self.assertEqual(1, len(callback.on_failure.call_args_list))
        error = callback.on_failure.call_args[0][0]
        self.assertEqual("File 'la' does not exist remotely", error)

        self.assertTrue(os.path.isdir(file_path))

    def test_command_delete_remote_forces_immediate_rescan(self):
        # Test that after a remote delete a remote scan is immediately done
        # Test this by simply setting the remote scan interval to a really large value
        # that would timeout the test if it wasn't forced
        self.context.config.controller.interval_ms_remote_scan = 90000

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "remote", "ra")
        self.assertTrue(os.path.isdir(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_REMOTE, "ra")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until file is removed from model
        while True:
            self.controller.process()
            call = listener.file_removed.call_args
            if call:
                file = call[0][0]
                self.assertEqual("ra", file.name)
                break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        self.assertFalse(os.path.exists(file_path))

    def test_command_delete_local_forces_immediate_rescan(self):
        # Test that after a local delete a local scan is immediately done
        # Test this by simply setting the local scan interval to a really large value
        # that would timeout the test if it wasn't forced
        self.context.config.controller.interval_ms_local_scan = 90000

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        file_path = os.path.join(TestController.temp_dir, "local", "la")
        self.assertTrue(os.path.isdir(file_path))

        # Send delete command
        command = Controller.Command(Controller.Command.Action.DELETE_LOCAL, "la")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until file is removed from model
        while True:
            self.controller.process()
            call = listener.file_removed.call_args
            if call:
                file = call[0][0]
                self.assertEqual("la", file.name)
                break
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()

        self.assertFalse(os.path.exists(file_path))

    @unittest.skip
    def test_download_with_excessive_connections(self):
        # Note: this test sometimes crashes the dbus
        #       reset with: sudo systemctl restart systemd-logind

        # Test excessive connections and a large LFTP status output
        #     - large files names to blow up the status
        #     - large max num connections, connections per file
        #     - download many files in parallel
        def create_large_file(_path, size):
            f = open(_path, "wb")
            f.seek(size - 1)
            f.write(b"\0")
            f.close()
            print("File size: ", os.stat(_path).st_size)

        # Create a bunch of large files that can be downloaded in chunks
        path = os.path.join(TestController.temp_dir, "remote", "large")
        local_path = os.path.join(TestController.temp_dir, "local", "large")
        os.mkdir(path)
        a_path = os.path.join(path, "a"*200 + ".txt")
        create_large_file(a_path, 20*1024*1024)
        b_path = os.path.join(path, "b"*200 + ".txt")
        create_large_file(b_path, 20*1024*1024)
        c_path = os.path.join(path, "c"*200 + ".txt")
        create_large_file(c_path, 20*1024*1024)
        d_path = os.path.join(path, "d"*200 + ".txt")
        create_large_file(d_path, 20*1024*1024)
        e_path = os.path.join(path, "e"*200 + ".txt")
        create_large_file(e_path, 20*1024*1024)
        f_path = os.path.join(path, "f"*200 + ".txt")
        create_large_file(f_path, 20*1024*1024)
        g_path = os.path.join(path, "g"*200 + ".txt")
        create_large_file(g_path, 20*1024*1024)
        h_path = os.path.join(path, "h"*200 + ".txt")
        create_large_file(h_path, 20*1024*1024)

        # White box hack: limit the rate of lftp so download doesn't finish
        #                 also set min-chunk size to a small value for lots of connections
        self.context.config.lftp.num_max_total_connections = 20
        self.context.config.lftp.num_max_connections_per_dir_file = 20
        self.context.config.lftp.num_max_parallel_files_per_download = 8

        # noinspection PyUnresolvedReferences
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 5*1024
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.min_chunk_size = "10"

        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue
        command = Controller.Command(Controller.Command.Action.QUEUE, "large")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until download starts
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                if new_file.name == "large" and new_file.state == ModelFile.State.DOWNLOADING:
                    break

        # Wait for a bit so we start getting large statuses
        start_time = datetime.now()
        elapsed_secs = 0
        while elapsed_secs < 5:
            print("Elapsed secs: ", elapsed_secs)
            self.controller.process()
            elapsed_secs = (datetime.now()-start_time).total_seconds()

        # Verify that download is still ongoing
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DOWNLOADING, files_dict["large"].state)

        # Stop the download
        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, "large"))
        self.controller.process()

        # Process until download stops
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("large", new_file.name)
                if new_file.state == ModelFile.State.DEFAULT:
                    break

        # Verify that download is stopped
        files = self.controller.get_model_files()
        files_dict = {f.name: f for f in files}
        self.assertEqual(ModelFile.State.DEFAULT, files_dict["large"].state)

        # Remove the files
        shutil.rmtree(path)
        shutil.rmtree(local_path)

    def test_stop_refresh_resume_completion_does_not_leave_stale_downloading_state(self):
        remote_name = "resume-refresh.bin"
        remote_size = 256 * 1024
        TestController.my_sparse_touch(remote_size, "remote", remote_name)

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 64 * 1024

        self.__wait_for_initial_model()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, remote_name))

        partial_file = None
        sidecar_seeded = False
        for _ in range(300):
            self.controller.process()
            partial_file = self.__find_model_file(remote_name)
            if partial_file is not None and \
                    partial_file.state == ModelFile.State.DOWNLOADING and \
                    partial_file.local_size is not None and \
                    partial_file.local_size > 0:
                if not sidecar_seeded:
                    local_incomplete_dir = os.path.join(TestController.temp_dir, "local", "incomplete")
                    os.makedirs(local_incomplete_dir, exist_ok=True)
                    status_payload = "size=262144\n0.pos=131072\n0.limit=262144\n"
                    for status_name in (
                        "{}.lftp-pget-status".format(remote_name),
                        "{}.lftp.lftp-pget-status".format(remote_name),
                    ):
                        with open(os.path.join(local_incomplete_dir, status_name), "w") as handle:
                            handle.write(status_payload)
                    self.controller._Controller__local_scan_process.force_scan()
                    self.controller._Controller__active_scan_process.force_scan()
                    sidecar_seeded = True
                if partial_file.is_stoppable:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for download to become stoppable before stop")

        stable_file_id = partial_file.file_id
        partial_size = partial_file.local_size

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, remote_name))

        stopped_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and file.local_size is not None and file.local_size >= partial_size,
            "Timed out waiting for stopped transfer state",
        )
        self.assertEqual(stable_file_id, stopped_file.file_id)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)
        self.__process_until(
            lambda: self.controller._Controller__active_downloading_file_names == [],
            "Timed out waiting for active downloading files to clear after stop",
        )
        for _ in range(20):
            self.controller.process()
        refreshed_file = self.__get_model_file(remote_name)
        self.assertEqual(stable_file_id, refreshed_file.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, refreshed_file.state)
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, remote_name))

        resumed_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DOWNLOADING and file.local_size is not None and file.local_size >= stopped_file.local_size,
            "Timed out waiting for resumed download progress",
            max_iterations=4000,
        )
        self.assertEqual(stable_file_id, resumed_file.file_id)

        final_target = os.path.join(TestController.temp_dir, "local", remote_name)
        staging_target = os.path.join(TestController.temp_dir, "local", "incomplete", remote_name)
        downloaded_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DOWNLOADED and os.path.exists(final_target),
            "Timed out waiting for resumed transfer to finish",
            max_iterations=4000,
        )

        for _ in range(20):
            self.controller.process()

        final_file = self.__get_model_file(remote_name)
        self.assertEqual(stable_file_id, final_file.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADED, final_file.state)
        self.assertEqual(remote_size, final_file.local_size)
        self.assertEqual(remote_size, downloaded_file.local_size)
        self.assertNotEqual(99, final_file.download_progress)
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertNotIn(stable_file_id, self.controller_persist.stopped_file_names)
        self.assertTrue(os.path.exists(final_target))
        self.assertFalse(os.path.exists(staging_target))
        self.assertTrue(cmp(
            os.path.join(TestController.temp_dir, "remote", remote_name),
            final_target
        ))

    def test_stop_requeue_stop_preserves_retained_progress_floor_across_repeat_cycles(self):
        remote_name = "repeat-stop.bin"
        remote_size = 256 * 1024
        TestController.my_sparse_touch(remote_size, "remote", remote_name)

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 64 * 1024

        self.__wait_for_initial_model()

        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, remote_name))

        partial_file = None
        sidecar_seeded = False
        for _ in range(300):
            self.controller.process()
            partial_file = self.__find_model_file(remote_name)
            if partial_file is not None and \
                    partial_file.state == ModelFile.State.DOWNLOADING and \
                    partial_file.local_size is not None and \
                    partial_file.local_size > 0:
                if not sidecar_seeded:
                    local_incomplete_dir = os.path.join(TestController.temp_dir, "local", "incomplete")
                    os.makedirs(local_incomplete_dir, exist_ok=True)
                    status_payload = "size=262144\n0.pos=131072\n0.limit=262144\n"
                    for status_name in (
                        "{}.lftp-pget-status".format(remote_name),
                        "{}.lftp.lftp-pget-status".format(remote_name),
                    ):
                        with open(os.path.join(local_incomplete_dir, status_name), "w") as handle:
                            handle.write(status_payload)
                    self.controller._Controller__local_scan_process.force_scan()
                    self.controller._Controller__active_scan_process.force_scan()
                    sidecar_seeded = True
                if partial_file.is_stoppable:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for download to become stoppable before first stop")

        stable_file_id = partial_file.file_id
        first_partial_size = partial_file.local_size
        self.assertIsNotNone(first_partial_size)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, remote_name))

        first_stopped_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and
            file.local_size is not None and file.local_size >= first_partial_size,
            "Timed out waiting for first stop to retain progress floor",
        )
        self.assertEqual(stable_file_id, first_stopped_file.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, first_stopped_file.state)
        self.assertIsNotNone(first_stopped_file.local_size)
        self.assertGreaterEqual(first_stopped_file.local_size, first_partial_size)
        self.__process_until(
            lambda: self.controller._Controller__active_downloading_file_names == [],
            "Timed out waiting for active downloading files to clear after first stop",
        )
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

        first_requeue_call_count = len(listener.file_updated.call_args_list)
        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, stable_file_id))

        requeued_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.file_id == stable_file_id and
            file.state in (ModelFile.State.QUEUED, ModelFile.State.DOWNLOADING) and
            file.local_size is not None and file.local_size >= first_stopped_file.local_size,
            "Timed out waiting for requeue to preserve retained progress floor",
            max_iterations=4000,
        )
        self.assertEqual(stable_file_id, requeued_file.file_id)
        self.assertIsNotNone(requeued_file.local_size)
        self.assertGreaterEqual(requeued_file.local_size, first_stopped_file.local_size)
        for call in listener.file_updated.call_args_list[first_requeue_call_count:]:
            new_file = call[0][1]
            if new_file.name == remote_name:
                self.assertIsNotNone(new_file.local_size)
                self.assertGreaterEqual(new_file.local_size, first_stopped_file.local_size)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, stable_file_id))

        second_stopped_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and
            file.local_size is not None and file.local_size >= requeued_file.local_size,
            "Timed out waiting for second stop to retain progress floor",
        )
        self.assertEqual(stable_file_id, second_stopped_file.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, second_stopped_file.state)
        self.assertIsNotNone(second_stopped_file.local_size)
        self.assertGreaterEqual(second_stopped_file.local_size, requeued_file.local_size)
        self.__process_until(
            lambda: self.controller._Controller__active_downloading_file_names == [],
            "Timed out waiting for active downloading files to clear after second stop",
        )
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

    def test_stop_refresh_missing_remote_window_preserves_retained_stopped_state(self):
        remote_name = "refresh-missing.bin"
        remote_size = 256 * 1024
        TestController.my_sparse_touch(remote_size, "remote", remote_name)

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 64 * 1024

        self.__wait_for_initial_model()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, remote_name))

        partial_file = None
        sidecar_seeded = False
        for _ in range(300):
            self.controller.process()
            partial_file = self.__find_model_file(remote_name)
            if partial_file is not None and \
                    partial_file.state == ModelFile.State.DOWNLOADING and \
                    partial_file.local_size is not None and \
                    partial_file.local_size > 0:
                if not sidecar_seeded:
                    local_incomplete_dir = os.path.join(TestController.temp_dir, "local", "incomplete")
                    os.makedirs(local_incomplete_dir, exist_ok=True)
                    status_payload = "size=262144\n0.pos=131072\n0.limit=262144\n"
                    for status_name in (
                        "{}.lftp-pget-status".format(remote_name),
                        "{}.lftp.lftp-pget-status".format(remote_name),
                    ):
                        with open(os.path.join(local_incomplete_dir, status_name), "w") as handle:
                            handle.write(status_payload)
                    self.controller._Controller__local_scan_process.force_scan()
                    self.controller._Controller__active_scan_process.force_scan()
                    sidecar_seeded = True
                if partial_file.is_stoppable:
                    break
            time.sleep(0.05)
        else:
            self.fail("Timed out waiting for download to become stoppable before stop")

        stable_file_id = partial_file.file_id
        partial_size = partial_file.local_size
        self.assertIsNotNone(partial_size)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, remote_name))

        stopped_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and
            file.local_size is not None and file.local_size >= partial_size,
            "Timed out waiting for stopped transfer state",
        )
        self.assertEqual(stable_file_id, stopped_file.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, stopped_file.state)
        self.assertIsNotNone(stopped_file.local_size)
        self.assertGreaterEqual(stopped_file.local_size, partial_size)
        self.__process_until(
            lambda: self.controller._Controller__active_downloading_file_names == [],
            "Timed out waiting for active downloading files to clear after stop",
        )
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

        remote_path = os.path.join(TestController.temp_dir, "remote", remote_name)
        remote_backup_path = remote_path + ".missing-window"
        shutil.move(remote_path, remote_backup_path)
        self.addCleanup(
            lambda: shutil.move(remote_backup_path, remote_path)
            if os.path.exists(remote_backup_path) and not os.path.exists(remote_path)
            else None
        )

        self.controller._Controller__remote_scan_process.force_scan()
        missing_remote_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and
            file.local_size is not None and file.local_size >= stopped_file.local_size,
            "Timed out waiting for stopped state to survive missing remote refresh window",
        )
        self.assertEqual(stable_file_id, missing_remote_file.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, missing_remote_file.state)
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertEqual(stopped_file.local_size, missing_remote_file.local_size)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

        shutil.move(remote_backup_path, remote_path)
        self.controller._Controller__remote_scan_process.force_scan()
        self.controller._Controller__local_scan_process.force_scan()
        self.controller._Controller__active_scan_process.force_scan()

        restored_remote_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and
            file.local_size is not None and file.local_size >= missing_remote_file.local_size,
            "Timed out waiting for stopped state to survive remote data return",
        )
        self.assertEqual(stable_file_id, restored_remote_file.file_id)
        self.assertEqual(ModelFile.State.DEFAULT, restored_remote_file.state)
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertEqual(stopped_file.local_size, restored_remote_file.local_size)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

    def test_stop_persisted_across_controller_restart_resumes_with_same_identity(self):
        remote_name = "resume-restart.bin"
        remote_size = 256 * 1024
        TestController.my_sparse_touch(remote_size, "remote", remote_name)

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # noinspection PyUnresolvedReferences
        self.controller._Controller__lftp.rate_limit = 64 * 1024

        self.__wait_for_initial_model()

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, remote_name))

        partial_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DOWNLOADING and file.local_size is not None and file.local_size >= 64 * 1024,
            "Timed out waiting for partial download progress before stop",
            max_iterations=3000,
        )
        partial_size = partial_file.local_size

        # Seed the pget sidecar so STOP sees the controller's current stoppable gate deterministically.
        local_incomplete_dir = os.path.join(TestController.temp_dir, "local", "incomplete")
        os.makedirs(local_incomplete_dir, exist_ok=True)
        status_payload = "size={}\n0.pos={}\n0.limit={}\n".format(remote_size, partial_size, remote_size)
        for status_name in (
            "{}.lftp-pget-status".format(remote_name),
            "{}.lftp.lftp-pget-status".format(remote_name),
        ):
            with open(os.path.join(local_incomplete_dir, status_name), "w") as handle:
                handle.write(status_payload)
        self.controller._Controller__local_scan_process.force_scan()
        self.controller._Controller__active_scan_process.force_scan()

        partial_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DOWNLOADING and
            file.local_size is not None and file.local_size >= partial_size and file.is_stoppable,
            "Timed out waiting for download to become stoppable before stop",
        )
        stable_file_id = partial_file.file_id

        self.controller.queue_command(Controller.Command(Controller.Command.Action.STOP, remote_name))
        stopped_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and file.local_size is not None and file.local_size >= partial_size,
            "Timed out waiting for stopped file state before restart",
        )
        self.assertEqual(stable_file_id, stopped_file.file_id)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

        staging_target = os.path.join(TestController.temp_dir, "local", "incomplete", remote_name)
        final_target = os.path.join(TestController.temp_dir, "local", remote_name)
        self.assertTrue(os.path.exists(staging_target))
        self.assertFalse(os.path.exists(final_target))

        self.controller.exit()
        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()

        self.__wait_for_initial_model()

        restarted_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DEFAULT and file.local_size is not None and file.local_size >= partial_size,
            "Timed out waiting for restarted controller to recover stopped transfer state",
        )
        self.assertEqual(stable_file_id, restarted_file.file_id)
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertIn(stable_file_id, self.controller_persist.stopped_file_names)

        self.controller.queue_command(Controller.Command(Controller.Command.Action.QUEUE, stable_file_id))

        resumed_file = self.__wait_for_model_file(
            remote_name,
            lambda file: file.state == ModelFile.State.DOWNLOADED and os.path.exists(final_target),
            "Timed out waiting for restarted controller to complete resumed transfer",
            max_iterations=4000,
        )

        for _ in range(20):
            self.controller.process()

        final_file = self.__get_model_file(remote_name)
        self.assertEqual(stable_file_id, final_file.file_id)
        self.assertEqual(ModelFile.State.DOWNLOADED, final_file.state)
        self.assertEqual(remote_size, final_file.local_size)
        self.assertEqual(remote_size, resumed_file.local_size)
        self.assertNotEqual(99, final_file.download_progress)
        self.assertEqual([], self.controller._Controller__active_downloading_file_names)
        self.assertNotIn(stable_file_id, self.controller_persist.stopped_file_names)
        self.assertTrue(os.path.exists(final_target))
        self.assertFalse(os.path.exists(staging_target))
        self.assertTrue(cmp(
            os.path.join(TestController.temp_dir, "remote", remote_name),
            final_target
        ))

    def test_password_auth(self):
        # Test password-based auth by downloading a file to completion
        self.context.config.lftp.use_ssh_key = False

        self.controller = Controller(self.context, self.controller_persist)
        self.controller.start()
        # wait for initial scan
        self.__wait_for_initial_model()

        # Ignore the initial state
        listener = DummyListener()
        self.controller.add_model_listener(listener)
        self.controller.process()

        # Setup mock
        listener.file_added = MagicMock()
        listener.file_updated = MagicMock()
        listener.file_removed = MagicMock()
        callback = DummyCommandCallback()
        callback.on_success = MagicMock()
        callback.on_failure = MagicMock()

        # Queue a download
        command = Controller.Command(Controller.Command.Action.QUEUE, "rc")
        command.add_callback(callback)
        self.controller.queue_command(command)
        # Process until done
        while True:
            self.controller.process()
            call = listener.file_updated.call_args
            if call:
                new_file = call[0][1]
                self.assertEqual("rc", new_file.name)
                if new_file.local_size == 10*1024:
                    break

        # Verify
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        callback.on_success.assert_called_once_with()
        callback.on_failure.assert_not_called()
        fcmp = cmp(os.path.join(TestController.temp_dir, "remote", "rc"),
                   os.path.join(TestController.temp_dir, "local", "rc"))
        self.assertTrue(fcmp)
