# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import tempfile
import shutil
import os
from unittest.mock import patch

from common import overrides, Persist, AppError, Localization


class DummyPersist(Persist):
    def __init__(self):
        self.my_content = None

    @classmethod
    @overrides(Persist)
    def from_str(cls: "DummyPersist", content: str) -> "DummyPersist":
        persist = DummyPersist()
        persist.my_content = content
        return persist

    @overrides(Persist)
    def to_str(self) -> str:
        return self.my_content


class TestPersist(unittest.TestCase):
    @overrides(unittest.TestCase)
    def setUp(self):
        # Create a temp directory
        self.temp_dir = tempfile.mkdtemp(prefix="test_persist")

    @overrides(unittest.TestCase)
    def tearDown(self):
        # Cleanup
        shutil.rmtree(self.temp_dir)

    def test_from_file(self):
        file_path = os.path.join(self.temp_dir, "persist")
        with open(file_path, "w") as f:
            f.write("some test content")
        persist = DummyPersist.from_file(file_path)
        self.assertEqual("some test content", persist.my_content)

    def test_from_file_non_existing(self):
        file_path = os.path.join(self.temp_dir, "persist")
        with self.assertRaises(AppError) as context:
            DummyPersist.from_file(file_path)
        self.assertEqual(Localization.Error.MISSING_FILE.format(file_path), str(context.exception))

    def test_to_file_non_existing(self):
        file_path = os.path.join(self.temp_dir, "persist")
        persist = DummyPersist()
        persist.my_content = "write out some content"
        persist.to_file(file_path)
        self.assertTrue(os.path.isfile(file_path))
        with open(file_path, "r") as f:
            self.assertEqual("write out some content", f.read())

    def test_to_file_overwrite(self):
        file_path = os.path.join(self.temp_dir, "persist")
        with open(file_path, "w") as f:
            f.write("pre-existing content")
            f.flush()
        persist = DummyPersist()
        persist.my_content = "write out some new content"
        persist.to_file(file_path)
        self.assertTrue(os.path.isfile(file_path))
        with open(file_path, "r") as f:
            self.assertEqual("write out some new content", f.read())

    def test_to_file_overwrite_creates_backup(self):
        file_path = os.path.join(self.temp_dir, "persist.json")
        with open(file_path, "w") as f:
            f.write("pre-existing content")
        persist = DummyPersist()
        persist.my_content = "write out some new content"

        persist.to_file(file_path)

        backup_dir = os.path.join(self.temp_dir, "backups")
        backups = os.listdir(backup_dir)
        self.assertEqual(1, len(backups))
        self.assertRegex(backups[0], r"^persist-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}\.json$")
        with open(os.path.join(backup_dir, backups[0]), "r") as f:
            self.assertEqual("pre-existing content", f.read())

    def test_to_file_prunes_old_backups(self):
        file_path = os.path.join(self.temp_dir, "persist")
        persist = DummyPersist()
        with open(file_path, "w") as f:
            f.write("initial")

        for index in range(12):
            persist.my_content = "content {}".format(index)
            persist.to_file(file_path)

        backup_dir = os.path.join(self.temp_dir, "backups")
        backups = sorted(os.listdir(backup_dir))
        self.assertEqual(10, len(backups))
        backup_contents = []
        for backup in backups:
            with open(os.path.join(backup_dir, backup), "r") as f:
                backup_contents.append(f.read())
        self.assertEqual("content 1", backup_contents[0])
        self.assertEqual("content 10", backup_contents[-1])
        self.assertNotIn("initial", backup_contents)
        self.assertNotIn("content 0", backup_contents)

    def test_to_file_continues_when_backup_fails(self):
        file_path = os.path.join(self.temp_dir, "persist")
        with open(file_path, "w") as f:
            f.write("pre-existing content")
        persist = DummyPersist()
        persist.my_content = "write out some new content"

        with patch("common.persist.shutil.copy2", side_effect=OSError("copy failed")):
            persist.to_file(file_path)

        with open(file_path, "r") as f:
            self.assertEqual("write out some new content", f.read())

    @unittest.skipUnless(os.name == "posix", "permission mode checks require POSIX semantics")
    def test_to_file_sets_0600_permissions(self):
        file_path = os.path.join(self.temp_dir, "persist_perms")
        persist = DummyPersist()
        persist.my_content = "sensitive content"
        persist.to_file(file_path)
        mode = os.stat(file_path).st_mode & 0o777
        self.assertEqual(0o600, mode, f"Expected 0600 permissions, got {oct(mode)}")

    @unittest.skipUnless(os.name == "posix", "permission mode checks require POSIX semantics")
    def test_from_file_tightens_permissive_permissions(self):
        file_path = os.path.join(self.temp_dir, "persist_tighten")
        with open(file_path, "w") as f:
            f.write("some content")
        os.chmod(file_path, 0o644)
        DummyPersist.from_file(file_path)
        mode = os.stat(file_path).st_mode & 0o777
        self.assertEqual(0o600, mode, f"Expected 0600 permissions after from_file(), got {oct(mode)}")

    @unittest.skipUnless(os.name == "posix", "permission mode checks require POSIX semantics")
    def test_from_file_ignores_permission_errors_when_chmod_is_unsupported(self):
        file_path = os.path.join(self.temp_dir, "persist_unportable")
        with open(file_path, "w") as f:
            f.write("some content")

        with patch("common.persist.os.chmod", side_effect=PermissionError("chmod unsupported")):
            persist = DummyPersist.from_file(file_path)

        self.assertEqual("some content", persist.my_content)

    @unittest.skipUnless(os.name == "posix", "permission mode checks require POSIX semantics")
    def test_to_file_overwrite_preserves_0600_permissions(self):
        file_path = os.path.join(self.temp_dir, "persist_overwrite_perms")
        persist = DummyPersist()
        persist.my_content = "first write"
        persist.to_file(file_path)
        mode = os.stat(file_path).st_mode & 0o777
        self.assertEqual(0o600, mode, f"Expected 0600 after first write, got {oct(mode)}")
        persist.my_content = "second write"
        persist.to_file(file_path)
        mode = os.stat(file_path).st_mode & 0o777
        self.assertEqual(0o600, mode, f"Expected 0600 after overwrite, got {oct(mode)}")
        with open(file_path, "r") as f:
            self.assertEqual("second write", f.read())

    @unittest.skipUnless(os.name == "posix", "permission mode checks require POSIX semantics")
    def test_to_file_hardens_backup_permissions(self):
        file_path = os.path.join(self.temp_dir, "persist_backup_perms")
        with open(file_path, "w") as f:
            f.write("pre-existing content")
        os.chmod(file_path, 0o644)
        persist = DummyPersist()
        persist.my_content = "updated content"

        persist.to_file(file_path)

        backup_dir = os.path.join(self.temp_dir, "backups")
        backups = os.listdir(backup_dir)
        self.assertEqual(1, len(backups))
        backup_dir_mode = os.stat(backup_dir).st_mode & 0o777
        self.assertEqual(0o700, backup_dir_mode, f"Expected backup directory 0700 permissions, got {oct(backup_dir_mode)}")
        backup_mode = os.stat(os.path.join(backup_dir, backups[0])).st_mode & 0o777
        self.assertEqual(0o600, backup_mode, f"Expected backup 0600 permissions, got {oct(backup_mode)}")

    @unittest.skipUnless(os.name == "posix", "permission mode checks require POSIX semantics")
    def test_to_file_overwrite_tightens_permissive_existing_file(self):
        file_path = os.path.join(self.temp_dir, "persist_overwrite_tighten_perms")
        with open(file_path, "w") as f:
            f.write("pre-existing content")
        os.chmod(file_path, 0o644)
        persist = DummyPersist()
        persist.my_content = "updated content"
        persist.to_file(file_path)
        mode = os.stat(file_path).st_mode & 0o777
        self.assertEqual(0o600, mode, f"Expected 0600 after overwriting permissive file, got {oct(mode)}")
        with open(file_path, "r") as f:
            self.assertEqual("updated content", f.read())
