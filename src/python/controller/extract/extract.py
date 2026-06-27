# Copyright 2017, Inderpreet Singh, All rights reserved.

import bz2
import gzip
import os
import shutil
import tarfile
import tempfile
import subprocess
import zipfile

from common import AppError


class ExtractError(AppError):
    """
    Indicates an extraction error
    """
    pass


class Extract:
    """
    Utility to extract archive files
    """
    __SUBPROCESS_TIMEOUT_SECS = 300

    @staticmethod
    def __detect_archive_kind(archive_path: str) -> str | None:
        if not os.path.isfile(archive_path):
            return None
        if zipfile.is_zipfile(archive_path):
            return "zip"
        if tarfile.is_tarfile(archive_path):
            return "tar"
        try:
            with open(archive_path, "rb") as handle:
                header = handle.read(8)
        except OSError:
            return None

        if header.startswith(b"Rar!\x1a\x07\x00") or header.startswith(b"Rar!\x1a\x07\x01\x00"):
            return "rar"
        if header.startswith(b"7z\xbc\xaf'\x1c"):
            return "7z"
        if header.startswith(b"\x1f\x8b"):
            return "gz"
        if header.startswith(b"BZh"):
            return "bz2"
        return None

    @staticmethod
    def is_archive(archive_path: str) -> bool:
        if not os.path.isfile(archive_path):
            return False
        return Extract.__detect_archive_kind(archive_path) is not None

    @staticmethod
    def is_archive_fast(archive_path: str) -> bool:
        """
        Fast version of is_archive that only looks at file extension
        May return false negatives
        :param archive_path:
        :return:
        """
        file_ext = os.path.splitext(os.path.basename(archive_path))[1]
        if file_ext:
            file_ext = file_ext[1:]  # remove the dot
            # noinspection SpellCheckingInspection
            return file_ext in [
                "7z",
                "bz2",
                "gz",
                "rar",
                "tar", "tgz", "tbz2",
                "zip", "zipx"
            ]
        else:
            return False

    @staticmethod
    def __check_member_path(member_name: str, real_out_dir: str) -> None:
        resolved = os.path.realpath(os.path.join(real_out_dir, member_name))
        try:
            common_path = os.path.commonpath([real_out_dir, resolved])
        except ValueError:
            common_path = ""
        if os.path.normcase(common_path) != os.path.normcase(real_out_dir):
            raise ExtractError(
                "Archive member '{}' escapes target directory '{}'".format(member_name, real_out_dir)
            )

    @staticmethod
    def __pre_validate_members(archive_path: str, out_dir_path: str) -> bool:
        real_out_dir = os.path.realpath(out_dir_path)

        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for info in zf.infolist():
                    if (info.external_attr >> 16) & 0xF000 == 0xA000:
                        raise ExtractError("Symlink rejected in archive: '{}'".format(info.filename))
                    Extract.__check_member_path(info.filename, real_out_dir)
            return True

        try:
            with tarfile.open(archive_path, "r:*") as tf:
                for member in tf.getmembers():
                    if member.issym() or member.islnk():
                        raise ExtractError("Symlink/hardlink rejected in archive: '{}'".format(member.name))
                    Extract.__check_member_path(member.name, real_out_dir)
            return True
        except tarfile.TarError:
            return False

    @staticmethod
    def __run_subprocess_extractor(command: list[str], tool_name: str) -> None:
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=Extract.__SUBPROCESS_TIMEOUT_SECS,
                check=False,
            )
        except FileNotFoundError as e:
            raise ExtractError("Required extraction tool not found: {}".format(tool_name)) from e
        except subprocess.TimeoutExpired as e:
            raise ExtractError(
                "{} failed after {}s timeout".format(tool_name, Extract.__SUBPROCESS_TIMEOUT_SECS)
            ) from e

        if result.returncode == 0:
            return

        raise ExtractError("{} failed with exit code {}".format(tool_name, result.returncode))

    @staticmethod
    def __extract_regular_file(source_handle, destination_path: str) -> None:
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with open(destination_path, "wb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle)

    @staticmethod
    def __extract_tar_payload(archive_path: str, payload_root: str) -> None:
        real_payload_root = os.path.realpath(payload_root)
        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf.getmembers():
                if member.issym() or member.islnk():
                    raise ExtractError("Symlink/hardlink rejected in archive: '{}'".format(member.name))
                Extract.__check_member_path(member.name, real_payload_root)

            for member in tf.getmembers():
                member_path = os.path.join(payload_root, member.name)
                if member.isdir():
                    os.makedirs(member_path, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ExtractError("Unsupported tar member type: '{}'".format(member.name))
                extracted_handle = tf.extractfile(member)
                if extracted_handle is None:
                    raise ExtractError("Unable to read tar member: '{}'".format(member.name))
                with extracted_handle:
                    Extract.__extract_regular_file(extracted_handle, member_path)

    @staticmethod
    def __extract_single_file_compressed(archive_kind: str, archive_path: str, payload_root: str) -> None:
        archive_name = os.path.basename(archive_path)
        if archive_kind == "gz":
            suffix = ".gz"
            opener = gzip.open
        elif archive_kind == "bz2":
            suffix = ".bz2"
            opener = bz2.open
        else:
            raise ExtractError("Unsupported compressed archive format: {}".format(archive_kind))

        if archive_name.lower().endswith(suffix):
            output_name = archive_name[:-len(suffix)]
        else:
            output_name = archive_name
        if not output_name:
            output_name = archive_name

        output_path = os.path.join(payload_root, output_name)
        with opener(archive_path, "rb") as source_handle:
            Extract.__extract_regular_file(source_handle, output_path)

    @staticmethod
    def __validate_staged_paths(temp_root: str, payload_root: str) -> None:
        real_payload_root = os.path.realpath(payload_root)
        for dirpath, dirnames, filenames in os.walk(temp_root):
            for name in dirnames + filenames:
                full_path = os.path.join(dirpath, name)
                real_path = os.path.realpath(full_path)
                try:
                    common_path = os.path.commonpath([real_payload_root, real_path])
                except ValueError:
                    common_path = ""
                if os.path.normcase(common_path) != os.path.normcase(real_payload_root):
                    raise ExtractError(
                        "Extracted path '{}' escapes staged extraction directory".format(full_path)
                    )
                if os.path.islink(full_path):
                    raise ExtractError("Link rejected in extracted archive: '{}'".format(full_path))
                if os.path.isfile(full_path):
                    try:
                        if os.stat(full_path).st_nlink > 1:
                            raise ExtractError("Hardlink rejected in extracted archive: '{}'".format(full_path))
                    except OSError as e:
                        raise ExtractError(str(e))

    @staticmethod
    def __merge_staged_payload(payload_root: str, out_dir_path: str) -> None:
        real_out_dir = os.path.realpath(out_dir_path)
        for dirpath, dirnames, filenames in os.walk(payload_root):
            rel_dir = os.path.relpath(dirpath, payload_root)
            target_dir = out_dir_path if rel_dir == "." else os.path.join(out_dir_path, rel_dir)
            target_dir_real = os.path.realpath(target_dir)
            try:
                common_path = os.path.commonpath([real_out_dir, target_dir_real])
            except ValueError:
                common_path = ""
            if os.path.normcase(common_path) != os.path.normcase(real_out_dir):
                raise ExtractError("Staged directory '{}' escapes target directory".format(target_dir))
            os.makedirs(target_dir, exist_ok=True)

            for dirname in dirnames:
                target_child_dir = os.path.join(target_dir, dirname)
                target_child_real = os.path.realpath(target_child_dir)
                try:
                    common_path = os.path.commonpath([real_out_dir, target_child_real])
                except ValueError:
                    common_path = ""
                if os.path.normcase(common_path) != os.path.normcase(real_out_dir):
                    raise ExtractError("Staged directory '{}' escapes target directory".format(target_child_dir))

            for filename in filenames:
                source_path = os.path.join(dirpath, filename)
                target_path = os.path.join(target_dir, filename)
                target_real = os.path.realpath(target_path)
                try:
                    common_path = os.path.commonpath([real_out_dir, target_real])
                except ValueError:
                    common_path = ""
                if os.path.normcase(common_path) != os.path.normcase(real_out_dir):
                    raise ExtractError("Staged file '{}' escapes target directory".format(target_path))
                os.replace(source_path, target_path)

    @staticmethod
    def __extract_staged_archive(archive_kind: str, archive_path: str, out_dir_path: str) -> None:
        parent_dir = os.path.dirname(os.path.realpath(out_dir_path)) or "."
        with tempfile.TemporaryDirectory(dir=parent_dir, prefix=".tmp_extract_") as temp_root:
            payload_root = os.path.join(temp_root, "payload")
            os.makedirs(payload_root)
            if archive_kind == "zip":
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extractall(payload_root)
            elif archive_kind == "tar":
                Extract.__extract_tar_payload(archive_path, payload_root)
            elif archive_kind in ("gz", "bz2"):
                Extract.__extract_single_file_compressed(archive_kind, archive_path, payload_root)
            elif archive_kind == "rar":
                Extract.__run_subprocess_extractor(
                    ["unrar", "x", "-o+", "-y", archive_path, payload_root],
                    "unrar",
                )
            elif archive_kind == "7z":
                Extract.__run_subprocess_extractor(
                    ["7z", "x", "-y", "-o{}".format(payload_root), archive_path],
                    "7z",
                )
            else:
                raise ExtractError("Unsupported archive format: {}".format(archive_path))
            Extract.__validate_staged_paths(temp_root, payload_root)
            Extract.__merge_staged_payload(payload_root, out_dir_path)

    @staticmethod
    def extract_archive(archive_path: str, out_dir_path: str):
        archive_kind = Extract.__detect_archive_kind(archive_path)
        if archive_kind is None:
            raise ExtractError("Path is not a valid archive: {}".format(archive_path))
        try:
            # Try to create the outdir path
            if not os.path.exists(out_dir_path):
                os.makedirs(out_dir_path)
            # Zip/tar get structured member prevalidation. Other formats are
            # contained by staging and validated before merge.
            if archive_kind in ("zip", "tar"):
                Extract.__pre_validate_members(archive_path, out_dir_path)
            Extract.__extract_staged_archive(archive_kind, archive_path, out_dir_path)
        except FileNotFoundError as e:
            raise ExtractError(str(e))
        except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as e:
            raise ExtractError(str(e))
