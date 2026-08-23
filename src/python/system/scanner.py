# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
from typing import Callable, List, Optional, Protocol
from datetime import datetime

# my libs
from common.error import AppError
from common.breadcrumb_trace import opaque_trace_correlation
from common.lftp_status import (
    MAX_LFTP_PGET_STATUS_BYTES,
    parse_lftp_pget_status,
    parse_lftp_pget_status_bytes,
)
from .file import SystemFile


class SystemScannerError(AppError):
    """
    Exception indicating a bad config value
    """
    pass


class _ScanEntry(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def path(self) -> str: ...
    def is_dir(self) -> bool: ...
    def stat(self) -> os.stat_result: ...


class PseudoDirEntry:
    def __init__(self, name: str, path: str, is_dir: bool, stat: os.stat_result):
        self.name = name
        self.path = path
        self._is_dir = is_dir
        self._stat = stat

    def is_dir(self) -> bool:
        return self._is_dir

    def stat(self) -> os.stat_result:
        return self._stat


LFTP_SIDECAR_TRACE_CATEGORY = "lftp.sidecar"
LFTP_SIDECAR_TRACE_SCHEMA = "lftp.sidecar.v1"
LFTP_SIDECAR_TRACE_CLASSIFICATIONS = frozenset({
    "absent", "valid", "malformed", "unknown",
})
LFTP_SIDECAR_TRACE_COVERAGE = frozenset({"known", "unknown"})
LFTP_SIDECAR_TRACE_ROLES = frozenset({
    "system", "local", "active", "multipath_active", "unknown",
})


def _breadcrumb_effectively_enabled(trace: object, category: str, level: str = "info") -> bool:
    """Check the cheap gate before constructing any sidecar evidence."""
    if trace is None:
        return False
    gate = getattr(trace, "is_effectively_enabled", None)
    if callable(gate):
        try:
            return bool(gate(category, level))
        except Exception:
            return False
    enabled = getattr(trace, "is_enabled", None)
    if callable(enabled):
        try:
            return bool(enabled())
        except Exception:
            return False
    return False


def lftp_sidecar_target_identity(scan_root: object, target_name: object) -> str:
    """Return one opaque identity for a root-relative sidecar target."""
    try:
        root = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(scan_root))))
        name = os.fsdecode(os.fspath(target_name))
        canonical = "lftp.sidecar.target.v1|{}|{}".format(root, name)
    except (TypeError, ValueError, OSError):
        canonical = "lftp.sidecar.target.v1|unknown"
    return opaque_trace_correlation(canonical)


def _record_lftp_sidecar_breadcrumb(
        breadcrumb_trace: object,
        classification: str,
        target_identity: object | Callable[[], str],
        *,
        status_only: Optional[bool],
        parser_coverage: str,
        scan_role: str,
) -> None:
    """Emit one privacy-safe, bounded scanner-side sidecar classification."""
    if classification not in LFTP_SIDECAR_TRACE_CLASSIFICATIONS:
        classification = "unknown"
    if parser_coverage not in LFTP_SIDECAR_TRACE_COVERAGE:
        parser_coverage = "unknown"
    if scan_role not in LFTP_SIDECAR_TRACE_ROLES:
        scan_role = "unknown"
    if type(status_only) is not bool:
        status_only = None

    # The gate deliberately precedes the opaque correlation construction and
    # all diagnostic-only payload work.  The emitter is best-effort: a broken
    # tracer must never change scan behavior.
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, LFTP_SIDECAR_TRACE_CATEGORY, "info"):
        return
    try:
        recorder = getattr(breadcrumb_trace, "record", None)
        if not callable(recorder):
            return
        if callable(target_identity):
            target_identity = target_identity()
        if not isinstance(target_identity, str):
            target_identity = "unknown"
        coalesce_key = opaque_trace_correlation(
            "lftp.sidecar.state|{}|{}|{}|{}|{}".format(
                target_identity,
                classification,
                "unknown" if status_only is None else str(status_only).lower(),
                parser_coverage,
                scan_role,
            ),
        )
        recorder(
            "system_scanner",
            "lftp_sidecar_classified",
            {
                "schema": LFTP_SIDECAR_TRACE_SCHEMA,
                "classification": classification,
                "status_only": status_only,
                "parser_coverage": parser_coverage,
                "scan_role": scan_role,
            },
            stage="lftp_sidecar_scan",
            event_type="diagnostic",
            category=LFTP_SIDECAR_TRACE_CATEGORY,
            level="info",
            corr_id=target_identity,
            trace_scope="flow",
            _coalesce_key=coalesce_key,
        )
    except Exception:
        return


class SystemScanner:
    """
    Scans system to generate list of files and sizes
    Children are returned in alphabetical order
    """
    __LFTP_STATUS_FILE_SUFFIX = ".lftp-pget-status"

    def __init__(self, path_to_scan: str):
        """
        :param path_to_scan: path to file or directory to scan
        """
        self.path_to_scan = path_to_scan
        self.exclude_prefixes: list[str] = []
        self.exclude_suffixes: list[str] = [SystemScanner.__LFTP_STATUS_FILE_SUFFIX]
        self.__lftp_temp_file_suffix: str | None = None
        self.__scan_had_errors = False
        self.__breadcrumb_trace: object = None
        self.__scan_role = "system"

    def set_breadcrumb_trace(self, breadcrumb_trace: object) -> None:
        """Set the worker-local sidecar breadcrumb emitter."""
        self.__breadcrumb_trace = breadcrumb_trace

    def set_scan_role(self, scan_role: str) -> None:
        """Set the fixed scanner role included in sidecar breadcrumbs."""
        self.__scan_role = scan_role if scan_role in LFTP_SIDECAR_TRACE_ROLES else "unknown"

    def add_exclude_prefix(self, prefix: str):
        """
        Exclude files that begin with the given prefix
        :param prefix:
        :return:
        """
        self.exclude_prefixes.append(prefix)

    def add_exclude_suffix(self, suffix: str):
        """
        Exclude files that end with the given suffix
        :param suffix:
        :return:
        """
        self.exclude_suffixes.append(suffix)

    def set_lftp_temp_suffix(self, suffix: str):
        """
        Set the suffix used by LFTP temp files
        Scanner will ignore the suffix and show these files with their
        original name
        :return:
        """
        self.__lftp_temp_file_suffix = suffix

    def scan(self) -> List[SystemFile]:
        """
        Scan the path to generate list of system files
        :return:
        """
        self.__scan_had_errors = False
        if not os.path.exists(self.path_to_scan):
            raise SystemScannerError("Path does not exist: {}".format(self.path_to_scan))
        elif not os.path.isdir(self.path_to_scan):
            raise SystemScannerError("Path is not a directory: {}".format(self.path_to_scan))
        return self.__create_children(self.path_to_scan)

    @property
    def scan_had_errors(self) -> bool:
        """Whether a race/permission error made this generation incomplete."""
        return self.__scan_had_errors

    def reset_scan_errors(self) -> None:
        self.__scan_had_errors = False

    def root_names(self) -> List[str]:
        """Return the filtered top-level manifest without scanning subtrees."""
        if not os.path.exists(self.path_to_scan):
            raise SystemScannerError("Path does not exist: {}".format(self.path_to_scan))
        if not os.path.isdir(self.path_to_scan):
            raise SystemScannerError("Path is not a directory: {}".format(self.path_to_scan))

        names: set[str] = set()
        for entry in os.scandir(self.path_to_scan):
            if entry.is_symlink() and entry.is_dir():
                continue
            if self.__excluded(entry.name):
                continue
            name = entry.name
            if self.__lftp_temp_file_suffix and name != self.__lftp_temp_file_suffix and \
                    name.endswith(self.__lftp_temp_file_suffix):
                name = name[:-len(self.__lftp_temp_file_suffix)]
            names.add(name)
        return sorted(names)

    def scan_single_if_present(self, name: str) -> Optional[SystemFile]:
        """Scan one top-level entry, returning ``None`` for a normal race."""
        path = os.path.join(self.path_to_scan, name)
        temp_path = (path + self.__lftp_temp_file_suffix) if self.__lftp_temp_file_suffix else None

        if os.path.exists(path):
            pass
        elif temp_path and os.path.isfile(temp_path):
            path = temp_path
        else:
            return None

        try:
            return self.__create_system_file(
                PseudoDirEntry(
                    name=name,
                    path=path,
                    is_dir=os.path.isdir(path) and not os.path.islink(path),
                    stat=os.stat(path)
                )
            )
        except FileNotFoundError:
            return None

    def scan_single(self, name: str) -> SystemFile:
        """
        Scan a single file/dir
        :param name:
        :return:
        """
        path = os.path.join(self.path_to_scan, name)
        temp_path = (path + self.__lftp_temp_file_suffix) if self.__lftp_temp_file_suffix else None

        if os.path.exists(path):
            pass
        elif temp_path and os.path.isfile(temp_path):
            path = temp_path
        else:
            raise SystemScannerError("Path does not exist: {}".format(path))

        try:
            return self.__create_system_file(
                PseudoDirEntry(
                    name=name,
                    path=path,
                    is_dir=os.path.isdir(path) and not os.path.islink(path),
                    stat=os.stat(path)
                )
            )
        except FileNotFoundError as error:
            # Preserve the explicit stat-race diagnostic for callers that
            # requested a specific root; optional progressive scans use
            # scan_single_if_present() instead.
            raise SystemScannerError("Failed to scan '{}': {}".format(path, error)) from error

    def __excluded(self, name: str) -> bool:
        return any(name.startswith(prefix) for prefix in self.exclude_prefixes) or \
            any(name.endswith(suffix) for suffix in self.exclude_suffixes)

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

    def __create_system_file(self, entry: _ScanEntry) -> SystemFile:
        """
        Creates a system file from a DirEntry.

        Note:
             Strips out any characters not-supported in utf-8. This prevents problems
             in other systems.

        Args:
            entry: DirEntry object

        Returns:
            The SystemFile object
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
            # Check if it's a partial lftp file, and if so, use the lftp
            # status to get the real file size
            lftp_status_file_path = entry.path + SystemScanner.__LFTP_STATUS_FILE_SUFFIX
            parsed_size = None
            sidecar_classification: Optional[str] = None
            parser_coverage = "unknown"

            def target_identity() -> str:
                target_identity_name = entry.name
                temp_suffix = self.__lftp_temp_file_suffix
                if temp_suffix is not None and target_identity_name != temp_suffix and \
                        target_identity_name.endswith(temp_suffix):
                    target_identity_name = target_identity_name[:-len(temp_suffix)]
                return lftp_sidecar_target_identity(self.path_to_scan, target_identity_name)

            try:
                sidecar_present = os.path.isfile(lftp_status_file_path)
                if sidecar_present:
                    parser_coverage = "known"
                    with open(lftp_status_file_path, "rb") as f:
                        parsed_status = parse_lftp_pget_status_bytes(
                            f.read(MAX_LFTP_PGET_STATUS_BYTES + 1)
                        )
                        parsed_size = parsed_status.covered_size if parsed_status is not None else None
                    sidecar_classification = "valid" if parsed_size is not None else "malformed"
                    if parsed_size is not None:
                        file_size = parsed_size
                elif self.__lftp_temp_file_suffix is not None and \
                        entry.path.endswith(self.__lftp_temp_file_suffix):
                    sidecar_classification = "absent"
            except (OSError, UnicodeError, ValueError, OverflowError):
                sidecar_classification = "unknown"
                _record_lftp_sidecar_breadcrumb(
                    self.__breadcrumb_trace,
                    sidecar_classification,
                    target_identity,
                    status_only=False,
                    parser_coverage=parser_coverage,
                    scan_role=self.__scan_role,
                )
                raise
            if sidecar_classification is not None:
                _record_lftp_sidecar_breadcrumb(
                    self.__breadcrumb_trace,
                    sidecar_classification,
                    target_identity,
                    status_only=False,
                    parser_coverage=parser_coverage,
                    scan_role=self.__scan_role,
                )
            status_sidecar_ready = parsed_size is not None
            if self.__lftp_temp_file_suffix is not None and \
                    entry.path.endswith(self.__lftp_temp_file_suffix) and \
                    parsed_size is None:
                # Temp files can be sparse or preallocated before LFTP writes
                # the status sidecar, so do not trust the raw on-disk size.
                file_size = 0
            # Check to see if this is a lftp temp file, and if so, use the real name
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
        children: list[SystemFile] = []
        # Files may get deleted while scanning, ignore the error
        try:
            entries = os.scandir(path)
        except PermissionError:
            self.__scan_had_errors = True
            return children
        except FileNotFoundError:
            return children
        for entry in entries:
            if entry.is_symlink() and entry.is_dir():
                continue
            # Skip excluded entries
            if self.__excluded(entry.name):
                continue

            try:
                sys_file = self.__create_system_file(entry)
            except FileNotFoundError:
                continue
            children.append(sys_file)
        children.sort(key=lambda fl: fl.name)
        return children

    @staticmethod
    def _lftp_status_file_size(status: str) -> Optional[int]:
        """
        Returns the real file size as indicated by an lftp status content
        :param status:
        :return:
        """
        parsed_status = parse_lftp_pget_status(status)
        return parsed_status.covered_size if parsed_status is not None else None
