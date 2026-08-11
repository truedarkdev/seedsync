# Copyright 2017, Inderpreet Singh, All rights reserved.
#
# Self-contained remote filesystem scanner.
# This script is uploaded to the remote server and executed via `python3`,
# so it must not depend on the local SeedSync package layout.

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import List, Optional, Protocol, Tuple, TypedDict


_STREAM_PREFIX = "SEEDSYNC_SCAN_V2\t"
# This is a wire limit, not a root-size limit.  Recursive roots are emitted as
# ordered node records below, so a large tree never becomes one buffered line.
_MAX_STREAM_RECORD_BYTES = 64 * 1024
_MAX_STREAM_NODE_BATCH_SIZE = 64


class SystemFileDataRequired(TypedDict):
    name: str
    size: int
    is_dir: bool


class SystemFileData(SystemFileDataRequired, total=False):
    time_created: Optional[str]
    time_modified: Optional[str]
    mtime_ns: int
    path_pair_id: Optional[str]
    path_pair_name: Optional[str]
    is_staging: bool
    has_staging_collision: bool
    children: List["SystemFileData"]


class ScanEntry(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def path(self) -> str: ...

    def is_dir(self) -> bool: ...

    def stat(self) -> os.stat_result: ...


class SystemFile:
    """
    Represents a system file or directory.
    """
    def __init__(self,
                 name: str,
                 size: int,
                 is_dir: bool = False,
                 time_created: Optional[datetime] = None,
                 time_modified: Optional[datetime] = None,
                 is_staging: bool = False,
                 mtime_ns: Optional[int] = None):
        if size < 0:
            raise ValueError("File size must be zero or greater")
        self.__name = name
        self.__size = size  # in bytes
        self.__is_dir = is_dir
        self.__timestamp_created = time_created
        self.__timestamp_modified = time_modified
        self.__mtime_ns = mtime_ns
        self.__children: List[SystemFile] = []
        self.__path_pair_id: Optional[str] = None
        self.__path_pair_name: Optional[str] = None
        self.__is_staging = is_staging
        self.__has_staging_collision = False
        self.__status_sidecar_ready = False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SystemFile):
            return NotImplemented
        return self.__dict__ == other.__dict__

    def __repr__(self) -> str:
        return str(self.__dict__)

    @property
    def name(self) -> str:
        return self.__name

    @property
    def size(self) -> int:
        return self.__size

    @property
    def is_dir(self) -> bool:
        return self.__is_dir

    @property
    def timestamp_created(self) -> Optional[datetime]:
        return self.__timestamp_created

    @property
    def timestamp_modified(self) -> Optional[datetime]:
        return self.__timestamp_modified

    @property
    def mtime_ns(self) -> Optional[int]:
        return self.__mtime_ns

    @property
    def children(self) -> List["SystemFile"]:
        return self.__children

    @property
    def path_pair_id(self) -> Optional[str]:
        return self.__path_pair_id

    @path_pair_id.setter
    def path_pair_id(self, path_pair_id: Optional[str]):
        if path_pair_id is not None and type(path_pair_id) != str:
            raise TypeError
        self.__path_pair_id = path_pair_id

    @property
    def path_pair_name(self) -> Optional[str]:
        return self.__path_pair_name

    @path_pair_name.setter
    def path_pair_name(self, path_pair_name: Optional[str]):
        if path_pair_name is not None and type(path_pair_name) != str:
            raise TypeError
        self.__path_pair_name = path_pair_name

    @property
    def is_staging(self) -> bool:
        return self.__is_staging

    @is_staging.setter
    def is_staging(self, is_staging: bool):
        if type(is_staging) != bool:
            raise TypeError
        self.__is_staging = is_staging

    @property
    def has_staging_collision(self) -> bool:
        return self.__has_staging_collision

    @has_staging_collision.setter
    def has_staging_collision(self, value: bool):
        if type(value) != bool:
            raise TypeError
        self.__has_staging_collision = value

    @property
    def status_sidecar_ready(self) -> bool:
        return self.__status_sidecar_ready

    @status_sidecar_ready.setter
    def status_sidecar_ready(self, status_sidecar_ready: bool):
        if type(status_sidecar_ready) != bool:
            raise TypeError
        self.__status_sidecar_ready = status_sidecar_ready

    def add_child(self, file: "SystemFile"):
        if not self.__is_dir:
            raise TypeError("Cannot add children to a file")
        self.__children.append(file)

    def to_dict(self) -> SystemFileData:
        d: SystemFileData = {
            "name": self.__name,
            "size": self.__size,
            "is_dir": self.__is_dir,
        }
        if self.__timestamp_created is not None:
            d["time_created"] = self.__timestamp_created.isoformat()
        if self.__timestamp_modified is not None:
            d["time_modified"] = self.__timestamp_modified.isoformat()
        if self.__mtime_ns is not None:
            d["mtime_ns"] = self.__mtime_ns
        if self.__path_pair_id is not None:
            d["path_pair_id"] = self.__path_pair_id
        if self.__path_pair_name is not None:
            d["path_pair_name"] = self.__path_pair_name
        if self.__is_staging:
            d["is_staging"] = True
        if self.__has_staging_collision:
            d["has_staging_collision"] = True
        if self.__children:
            d["children"] = [child.to_dict() for child in self.__children]
        return d

    @classmethod
    def from_dict(cls, data: SystemFileData) -> "SystemFile":
        time_created = None
        time_modified = None
        if "time_created" in data and data["time_created"] is not None:
            time_created = datetime.fromisoformat(data["time_created"])
        if "time_modified" in data and data["time_modified"] is not None:
            time_modified = datetime.fromisoformat(data["time_modified"])
        system_file = cls(
            name=data["name"],
            size=data["size"],
            is_dir=data.get("is_dir", False),
            time_created=time_created,
            time_modified=time_modified,
            mtime_ns=data.get("mtime_ns"),
            is_staging=data.get("is_staging", False),
        )
        system_file.path_pair_id = data.get("path_pair_id")
        system_file.path_pair_name = data.get("path_pair_name")
        system_file.has_staging_collision = data.get("has_staging_collision", False)
        for child_data in data.get("children", []):
            system_file.add_child(cls.from_dict(child_data))
        return system_file


class SystemScannerError(Exception):
    """
    Exception indicating a bad config value.
    """
    pass


class PseudoDirEntry:
    def __init__(self, name: str, path: str, is_dir: bool, stat_result: os.stat_result):
        self.name = name
        self.path = path
        self._is_dir = is_dir
        self._stat = stat_result

    def is_dir(self) -> bool:
        return self._is_dir

    def stat(self) -> os.stat_result:
        return self._stat


class SystemScanner:
    """
    Scans a filesystem to generate a list of files and sizes.
    Children are returned in alphabetical order.
    """
    __LFTP_STATUS_FILE_SUFFIX = ".lftp-pget-status"

    def __init__(self, path_to_scan: str):
        """
        :param path_to_scan: path to file or directory to scan
        """
        self.path_to_scan = path_to_scan
        self.exclude_prefixes: List[str] = []
        self.exclude_suffixes: List[str] = [SystemScanner.__LFTP_STATUS_FILE_SUFFIX]
        self.__lftp_temp_file_suffix: Optional[str] = None
        self.scan_had_errors = False

    def add_exclude_prefix(self, prefix: str):
        """
        Exclude files that begin with the given prefix.
        """
        self.exclude_prefixes.append(prefix)

    def add_exclude_suffix(self, suffix: str):
        """
        Exclude files that end with the given suffix.
        """
        self.exclude_suffixes.append(suffix)

    def set_lftp_temp_suffix(self, suffix: str):
        """
        Set the suffix used by LFTP temp files.
        """
        self.__lftp_temp_file_suffix = suffix

    def scan(self) -> List[SystemFile]:
        """
        Scan the path to generate list of system files.
        """
        self.scan_had_errors = False
        if not os.path.exists(self.path_to_scan):
            raise SystemScannerError("Path does not exist: {}".format(self.path_to_scan))
        elif not os.path.isdir(self.path_to_scan):
            raise SystemScannerError("Path is not a directory: {}".format(self.path_to_scan))
        return self.__create_children(self.path_to_scan)

    def root_names(self) -> List[str]:
        self.scan_had_errors = False
        if not os.path.exists(self.path_to_scan):
            raise SystemScannerError("Path does not exist: {}".format(self.path_to_scan))
        if not os.path.isdir(self.path_to_scan):
            raise SystemScannerError("Path is not a directory: {}".format(self.path_to_scan))
        names: set[str] = set()
        try:
            for entry in os.scandir(self.path_to_scan):
                if entry.is_symlink() and entry.is_dir():
                    continue
                if self.__excluded(entry.name):
                    continue
                names.add(entry.name)
        except PermissionError as e:
            self.scan_had_errors = True
            raise SystemScannerError(
                "Permission denied while scanning: {}".format(self.path_to_scan)
            ) from e
        return sorted(names)

    def scan_single_if_present(self, name: str) -> Optional[SystemFile]:
        path = os.path.join(self.path_to_scan, name)
        temp_path = (path + self.__lftp_temp_file_suffix) if self.__lftp_temp_file_suffix else None
        if os.path.exists(path):
            pass
        elif temp_path and os.path.isfile(temp_path):
            path = temp_path
        else:
            return None
        try:
            return self.__create_system_file(PseudoDirEntry(
                name=name,
                path=path,
                is_dir=os.path.isdir(path) and not os.path.islink(path),
                stat_result=os.stat(path),
            ))
        except FileNotFoundError:
            return None
        except PermissionError:
            self.scan_had_errors = True
            return None

    def __excluded(self, name: str) -> bool:
        return any(name.startswith(prefix) for prefix in self.exclude_prefixes) or \
            any(name.endswith(suffix) for suffix in self.exclude_suffixes)

    def scan_single(self, name: str) -> SystemFile:
        """
        Scan a single file/dir.
        """
        path = os.path.join(self.path_to_scan, name)
        temp_path = (path + self.__lftp_temp_file_suffix) if self.__lftp_temp_file_suffix else None

        if os.path.exists(path):
            pass
        elif temp_path and os.path.isfile(temp_path):
            path = temp_path
        else:
            raise SystemScannerError("Path does not exist: {}".format(path))

        return self.__create_system_file(
            PseudoDirEntry(
                name=name,
                path=path,
                is_dir=os.path.isdir(path) and not os.path.islink(path),
                stat_result=os.stat(path)
            )
        )

    @staticmethod
    def __get_created_time(stat_result: os.stat_result) -> Optional[datetime]:
        try:
            birthtime = getattr(stat_result, "st_birthtime")
            if isinstance(birthtime, (int, float)):
                return datetime.fromtimestamp(birthtime)
        except (AttributeError, OSError, OverflowError, TypeError, ValueError):
            pass
        try:
            return datetime.fromtimestamp(stat_result.st_ctime)
        except (AttributeError, OSError, OverflowError, TypeError, ValueError):
            return None

    @staticmethod
    def __get_mtime_ns(stat_result: os.stat_result) -> Optional[int]:
        try:
            mtime_ns = getattr(stat_result, "st_mtime_ns")
            if type(mtime_ns) is int:
                return mtime_ns
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        try:
            mtime = stat_result.st_mtime
            if isinstance(mtime, (int, float)):
                return int(mtime * 1_000_000_000)
        except (AttributeError, OSError, OverflowError, TypeError, ValueError):
            pass
        return None

    def __create_system_file(self, entry: ScanEntry) -> SystemFile:
        """
        Creates a system file from a DirEntry.
        """
        entry_stat = entry.stat()
        if entry.is_dir():
            sub_children = self.__create_children(entry.path)
            name = entry.name.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
            size = sum(sub_child.size for sub_child in sub_children)
            time_created = SystemScanner.__get_created_time(entry_stat)
            time_modified = datetime.fromtimestamp(entry_stat.st_mtime)
            mtime_ns = SystemScanner.__get_mtime_ns(entry_stat)
            sys_file = SystemFile(name,
                                  size,
                                  True,
                                  time_created=time_created,
                                  time_modified=time_modified,
                                  mtime_ns=mtime_ns)
            for sub_child in sub_children:
                sys_file.add_child(sub_child)
        else:
            file_size = entry_stat.st_size
            lftp_status_file_path = entry.path + SystemScanner.__LFTP_STATUS_FILE_SUFFIX
            parsed_size = None
            if os.path.isfile(lftp_status_file_path):
                with open(lftp_status_file_path, "r", encoding="utf-8") as f:
                    parsed_size = SystemScanner._lftp_status_file_size(f.read())
                    if parsed_size is not None:
                        file_size = parsed_size
            status_sidecar_ready = parsed_size is not None
            if self.__lftp_temp_file_suffix is not None and \
                    entry.path.endswith(self.__lftp_temp_file_suffix) and \
                    parsed_size is None:
                # Temp files can be sparse or preallocated before LFTP writes
                # the status sidecar, so do not trust the raw on-disk size.
                file_size = 0
            file_name = entry.name.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
            if self.__lftp_temp_file_suffix is not None and \
                    file_name != self.__lftp_temp_file_suffix and \
                    file_name.endswith(self.__lftp_temp_file_suffix):
                file_name = file_name[:-len(self.__lftp_temp_file_suffix)]
            time_created = SystemScanner.__get_created_time(entry_stat)
            time_modified = datetime.fromtimestamp(entry_stat.st_mtime)
            mtime_ns = SystemScanner.__get_mtime_ns(entry_stat)
            sys_file = SystemFile(file_name,
                                  file_size,
                                  False,
                                  time_created=time_created,
                                  time_modified=time_modified,
                                  mtime_ns=mtime_ns)
            sys_file.status_sidecar_ready = status_sidecar_ready
        return sys_file

    def __create_children(self, path: str) -> List[SystemFile]:
        children: List[SystemFile] = []
        # Files may get deleted while scanning, ignore the error.
        try:
            entries = os.scandir(path)
        except FileNotFoundError:
            return children
        except PermissionError:
            self.scan_had_errors = True
            return children
        try:
            try:
                for entry in entries:
                    try:
                        if entry.is_symlink() and entry.is_dir():
                            continue

                        if self.__excluded(entry.name):
                            continue

                        sys_file = self.__create_system_file(entry)
                    except FileNotFoundError:
                        continue
                    except PermissionError:
                        self.scan_had_errors = True
                        continue
                    children.append(sys_file)
            except PermissionError:
                self.scan_had_errors = True
        finally:
            entries.close()
        children.sort(key=lambda fl: fl.name)
        return children

    @staticmethod
    def _lftp_status_file_size(status: str) -> Optional[int]:
        """
        Returns the real file size as indicated by an lftp status content.
        """
        size_pattern_m = re.compile(r"^size=(\d+)$")
        pos_pattern_m = re.compile(r"^\d+\.pos=(\d+)$")
        limit_pattern_m = re.compile(r"^\d+\.limit=(\d+)$")
        lines = [s.strip() for s in status.splitlines()]
        lines = list(filter(None, lines))  # remove blank lines
        if not lines:
            return None

        empty_size = 0
        # First line should be a size.
        result = size_pattern_m.search(lines[0])
        if not result:
            return None
        total_size = int(result.group(1))
        lines.pop(0)
        while lines:
            # There should be pairs of lines.
            if len(lines) < 2:
                return None
            result_pos = pos_pattern_m.search(lines[0])
            result_limit = limit_pattern_m.search(lines[1])
            if not result_pos or not result_limit:
                return None
            pos = int(result_pos.group(1))
            limit = int(result_limit.group(1))
            if pos > total_size or limit > total_size or limit < pos:
                return None
            empty_size += limit - pos
            lines.pop(0)
            lines.pop(0)

        if empty_size > total_size:
            return None

        return total_size - empty_size


def _stream_shallow_file_data(file: SystemFile) -> SystemFileData:
    """Serialize one node without recursively materializing its descendants."""
    data: SystemFileData = {
        "name": file.name,
        "size": file.size,
        "is_dir": file.is_dir,
    }
    if file.timestamp_created is not None:
        data["time_created"] = file.timestamp_created.isoformat()
    if file.timestamp_modified is not None:
        data["time_modified"] = file.timestamp_modified.isoformat()
    if file.mtime_ns is not None:
        data["mtime_ns"] = file.mtime_ns
    if file.path_pair_id is not None:
        data["path_pair_id"] = file.path_pair_id
    if file.path_pair_name is not None:
        data["path_pair_name"] = file.path_pair_name
    if file.is_staging:
        data["is_staging"] = True
    if file.has_staging_collision:
        data["has_staging_collision"] = True
    return data


def _encode_stream_record(record: dict) -> str:
    line = _STREAM_PREFIX + json.dumps(record, separators=(",", ":")) + "\n"
    if len(line.encode("utf-8")) > _MAX_STREAM_RECORD_BYTES:
        raise SystemScannerError("Scan stream record exceeded {} bytes".format(_MAX_STREAM_RECORD_BYTES))
    return line


def _write_stream_record(record: dict) -> None:
    sys.stdout.write(_encode_stream_record(record))
    sys.stdout.flush()


def _write_stream_manifest(root_names: List[str]) -> None:
    """Emit a bounded manifest before progressive root records."""
    _write_stream_record({"type": "manifest_begin", "count": len(root_names)})
    batch: List[str] = []
    for name in root_names:
        candidate = batch + [name]
        try:
            _encode_stream_record({"type": "manifest_names", "names": candidate})
        except SystemScannerError:
            if not batch:
                raise
            _write_stream_record({"type": "manifest_names", "names": batch})
            batch = [name]
            _encode_stream_record({"type": "manifest_names", "names": batch})
        else:
            batch = candidate
    if batch:
        _write_stream_record({"type": "manifest_names", "names": batch})
    _write_stream_record({"type": "manifest_end"})


def _write_stream_root(root_id: int, root: SystemFile, node_batch_size: int) -> None:
    """Emit a recursive root as ordered, bounded node batches.

    Parent ids point only into the current root's active preorder stack.  The
    receiver can therefore build the final tree without retaining JSON
    fragments or a node-id map for the whole root.
    """
    _write_stream_record({"type": "root_begin", "id": root_id, "name": root.name})
    next_node_id = 0
    node_batch: List[dict] = []

    def flush_node_batch() -> None:
        if node_batch:
            _write_stream_record({"type": "root_nodes", "root": root_id, "nodes": node_batch})
            node_batch.clear()

    pending_nodes: List[Tuple[SystemFile, Optional[int]]] = [(root, None)]
    while pending_nodes:
        node, parent_id = pending_nodes.pop()
        node_id = next_node_id
        next_node_id += 1
        node_record = {
            "id": node_id,
            "parent": parent_id,
            "file": _stream_shallow_file_data(node),
        }
        # Keep the established small CLI batch cap for uploaded-helper
        # compatibility, while also proving each record remains below the V2
        # 64 KiB wire limit before it is buffered.
        if len(node_batch) >= node_batch_size:
            flush_node_batch()
        candidate = node_batch + [node_record]
        try:
            _encode_stream_record({"type": "root_nodes", "root": root_id, "nodes": candidate})
        except SystemScannerError:
            if not node_batch:
                raise
            flush_node_batch()
            _encode_stream_record({"type": "root_nodes", "root": root_id, "nodes": [node_record]})
            node_batch.append(node_record)
        else:
            node_batch.append(node_record)
        pending_nodes.extend((child, node_id) for child in reversed(node.children))

    flush_node_batch()
    _write_stream_record({"type": "root_end", "id": root_id, "nodes": next_node_id})


if __name__ == "__main__":
    if sys.hexversion < 0x03080000:
        sys.exit("Python 3.8 or later is required to run this program.")

    parser = argparse.ArgumentParser(description="File size scanner")
    parser.add_argument("path", help="Path of the root directory to scan")
    parser.add_argument("-e", "--exclude-hidden", action="store_true", default=False,
                        help="Exclude hidden files")
    parser.add_argument("-H", "--human-readable", action="store_true", default=False,
                        help="Human readable output")
    parser.add_argument("--stream", action="store_true", default=False,
                        help="Emit bounded SeedSync scan protocol records")
    parser.add_argument("--stream-batch-size", type=int, default=_MAX_STREAM_NODE_BATCH_SIZE,
                        help="Maximum V2 nodes per bounded record")
    args = parser.parse_args()

    scanner = SystemScanner(args.path)
    if args.exclude_hidden:
        scanner.add_exclude_prefix(".")
    try:
        if args.stream:
            if args.stream_batch_size < 1 or args.stream_batch_size > _MAX_STREAM_NODE_BATCH_SIZE:
                parser.error("--stream-batch-size must be between 1 and {}".format(_MAX_STREAM_NODE_BATCH_SIZE))
            root_names = scanner.root_names()
            _write_stream_manifest(root_names)
            emitted_root_id = 0
            for root_name in root_names:
                root_file = scanner.scan_single_if_present(root_name)
                if root_file is None:
                    continue
                _write_stream_root(emitted_root_id, root_file, args.stream_batch_size)
                emitted_root_id += 1
            if scanner.scan_had_errors:
                raise SystemScannerError(
                    "Permission denied while scanning: {}".format(args.path)
                )
            _write_stream_record({"type": "complete"})
            root_files = []
        else:
            root_files = scanner.scan()
            if scanner.scan_had_errors:
                raise SystemScannerError(
                    "Permission denied while scanning: {}".format(args.path)
                )
    except SystemScannerError as e:
        sys.exit("SystemScannerError: {}".format(str(e)))

    if args.stream:
        pass
    elif args.human_readable:
        def print_file(file: SystemFile, level: int):
            sys.stdout.write("  " * level)
            sys.stdout.write("{} {} {}\n".format(
                file.name,
                "d" if file.is_dir else "f",
                file.size
            ))
            for child in file.children:
                print_file(child, level + 1)

        for root_file in root_files:
            print_file(root_file, 0)
    else:
        json_list = [file.to_dict() for file in root_files]
        sys.stdout.write(json.dumps(json_list))
