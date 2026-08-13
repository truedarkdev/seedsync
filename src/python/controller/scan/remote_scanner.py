# Copyright 2017, Inderpreet Singh, All rights reserved.

import base64
import logging
import json
import codecs
import inspect
import re
import errno
import hashlib
import os
import posixpath
import tempfile
import time
from collections.abc import Callable
from typing import List, Optional, cast
import shlex

from .scanner_process import IScanner, ScannerError, ScanProgressCallback
from common import overrides, Localization, escape_remote_path_for_shell
from ssh import Sshcp, SshcpError, TRANSIENT_ERROR_PATTERNS
from common.performance_diagnostics import (
    DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION,
    DURATION_REMOTE_SCAN_STREAM_PARSING,
    PerformanceDiagnosticsCollector,
)
from system import SystemFile


class _RemoteScanLeaseHandle:
    """One process-held advisory lock handle for a shared scan lease path."""

    def __init__(self, path: str):
        self.__path = path
        self.__descriptor: Optional[int] = None

    def acquire(self) -> None:
        if self.__descriptor is not None:
            raise RuntimeError("Remote scan lease is already held")
        descriptor = os.open(self.__path, os.O_CREAT | os.O_RDWR)
        try:
            # Windows byte-range locking requires a non-empty file and starts
            # at the current descriptor position.
            if os.name == "nt":
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "posix":
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if getattr(error, "errno", None) not in (13, 36,  errno.EACCES, errno.EAGAIN):
                            raise
                        time.sleep(0.05)
            self.__descriptor = descriptor
        except Exception:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor = self.__descriptor
        self.__descriptor = None
        if descriptor is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            os.close(descriptor)

    def __enter__(self) -> "_RemoteScanLeaseHandle":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class RemoteScanLease:
    """Spawn-safe path identity for an OS-held, crash-released scan lease."""

    def __init__(self, path: str):
        self.path = os.fspath(path)

    @classmethod
    def create(cls, directory: Optional[str] = None) -> "RemoteScanLease":
        if isinstance(directory, str) and directory and os.path.isdir(directory):
            path = os.path.join(directory, ".seedsync-remote-scan.lock")
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_RDWR)
                os.close(descriptor)
                if os.name == "posix":
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass
                return cls(path)
            except OSError:
                # A read-only or transiently unavailable config directory
                # should not prevent startup; the per-controller temp lease
                # still serializes all generations owned by this process.
                pass
        descriptor, path = tempfile.mkstemp(prefix=".seedsync-remote-scan-", suffix=".lock")
        os.close(descriptor)
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return cls(path)

    def hold(self) -> _RemoteScanLeaseHandle:
        return _RemoteScanLeaseHandle(self.path)


class RemoteScanner(IScanner):
    """
    Scanner implementation to scan the remote filesystem
    """

    _SAFE_TILDE_PREFIX = re.compile(r"^~(?:[A-Za-z0-9_.-]+)?(?:/|$)")
    _SCP_DEST_OPEN_PERMISSION_DENIED = re.compile(
        r"^scp:\s+dest open\s+(?P<path>.+):\s+(?:-\s+)?permission denied$",
        re.IGNORECASE
    )
    _SCP_PATH_PERMISSION_DENIED = re.compile(
        r"^scp:\s+(?P<path>.+):\s+(?:-\s+)?permission denied$",
        re.IGNORECASE
    )
    # V2 records are newline-delimited. Keep each encoded record bounded so a
    # malformed unterminated stream cannot retain arbitrary callback data in
    # the incremental decoder buffer.
    _MAX_V2_STREAM_RECORD_BYTES = 64 * 1024
    _MAX_V2_ROOT_NODES_PER_RECORD = 64
    # Coalesce only completed framed roots waiting for publication.  The age
    # check runs on root completion instead of using a timer/thread, keeping
    # stream parsing and active transfer status independent.
    _ROOT_PROGRESS_BATCH_SIZE = 8
    _ROOT_PROGRESS_MAX_AGE_SECONDS = 0.1
    _MAX_KNOWN_ROOT_FINGERPRINT_BYTES = 32 * 1024

    @classmethod
    def _validate_v2_stream_record_bytes(cls, record: bytes) -> None:
        """Validate one complete or partial V2 wire record before buffering."""
        cls._validate_v2_stream_record_size(len(record))

    @classmethod
    def _validate_v2_stream_record_size(cls, size: int) -> None:
        if size > cls._MAX_V2_STREAM_RECORD_BYTES:
            raise ValueError("scan stream V2 record exceeded limit")

    @staticmethod
    def _is_transient_ssh_error(error: SshcpError) -> bool:
        error_message = str(error)
        return any(pattern in error_message for pattern in TRANSIENT_ERROR_PATTERNS)

    @staticmethod
    def __is_unsupported_stream_option_error(error: SshcpError) -> bool:
        message = str(error).lower()
        return "--stream" in message and any(
            marker in message
            for marker in ("unknown option", "unrecognized option", "illegal option", "usage:")
        )

    @staticmethod
    def _normalize_scp_error_path(path: str) -> str:
        path = path.strip()
        if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"'}:
            return path[1:-1]
        return path

    @staticmethod
    def _extract_permission_denied_scp_path(line: str) -> Optional[str]:
        for pattern in (
            RemoteScanner._SCP_DEST_OPEN_PERMISSION_DENIED,
            RemoteScanner._SCP_PATH_PERMISSION_DENIED,
        ):
            match = pattern.match(line.strip())
            if match:
                return RemoteScanner._normalize_scp_error_path(match.group("path"))
        return None

    @staticmethod
    def _is_scanfs_destination_write_denied(error: SshcpError, remote_path: str) -> bool:
        error_message = str(error).strip()
        if "permission denied" not in error_message.lower():
            return False

        for line in error_message.splitlines():
            denied_path = RemoteScanner._extract_permission_denied_scp_path(line)
            if denied_path == remote_path:
                return True
        return False

    @staticmethod
    def _normalize_remote_python_path(remote_python_path: object) -> str:
        if remote_python_path is None:
            return "python3"
        if not isinstance(remote_python_path, str):
            remote_python_path = str(remote_python_path)
        normalized = remote_python_path.strip()
        if not normalized:
            return "python3"
        return normalized

    @staticmethod
    def __is_python_shebang_helper(shebang: str) -> bool:
        # Treat ambiguous or malformed shebangs as Python helpers so we stay on the configured
        # interpreter path unless the script clearly signals a non-Python helper.
        try:
            shebang_tokens = shlex.split(shebang)
        except ValueError:
            return True

        if not shebang_tokens:
            return True

        interpreter_tokens = shebang_tokens
        if os.path.basename(shebang_tokens[0]).lower() == "env":
            interpreter_tokens = [token for token in shebang_tokens[1:] if not token.startswith("-")]
            if not interpreter_tokens:
                return True

        for token in interpreter_tokens:
            interpreter = os.path.basename(token).lower()
            if interpreter.startswith("python") or interpreter.startswith("pypy"):
                return True

        return False

    @staticmethod
    def __should_execute_scanfs_directly(local_path_to_scan_script: Optional[str]) -> bool:
        if not isinstance(local_path_to_scan_script, str) or not local_path_to_scan_script:
            return False

        try:
            with open(local_path_to_scan_script, "rb") as handle:
                file_prefix = handle.read(256)
        except (OSError, TypeError, ValueError):
            return False

        if file_prefix.startswith(b"\x7fELF"):
            return True

        if not file_prefix.startswith(b"#!"):
            return False

        first_line = file_prefix.splitlines()[0].decode("utf-8", errors="ignore").strip()
        shebang = first_line[2:].strip()
        if not shebang:
            return False

        return not RemoteScanner.__is_python_shebang_helper(shebang)

    def __init__(self,
                 remote_address: str,
                 remote_username: str,
                 remote_password: Optional[str],
                 remote_port: int,
                 remote_path_to_scan: str,
                 local_path_to_scan_script: str,
                 remote_path_to_scan_script: str,
                 remote_python_path: Optional[str] = "python3",
                 path_pair_id: Optional[str] = None,
                 path_pair_name: Optional[str] = None,
                 remote_scan_lease: Optional[RemoteScanLease] = None,
                 performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None):
        self.logger = logging.getLogger("RemoteScanner")
        self.__remote_path_to_scan = remote_path_to_scan
        self.__local_path_to_scan_script = local_path_to_scan_script
        self.__remote_path_to_scan_script = remote_path_to_scan_script
        self.__remote_python_path = remote_python_path
        self.__remote_scan_lease = remote_scan_lease
        self.__performance_diagnostics = performance_diagnostics
        self.__ssh = Sshcp(host=remote_address,
                           port=remote_port,
                           user=remote_username,
                           password=remote_password,
                           performance_diagnostics=performance_diagnostics)
        self.__first_run = True
        self.__path_pair_id = path_pair_id
        self.__path_pair_name = path_pair_name
        self.__progress_callback: Optional[ScanProgressCallback] = None
        self.__progress_callback_supports_fingerprints = False
        self.__accepted_root_fingerprints: dict[str, str] = {}

        # Append scan script name to remote path if not there already
        if self.__is_valid_local_script_path(self.__local_path_to_scan_script) and \
                self.__is_valid_remote_script_path(self.__remote_path_to_scan_script):
            script_name = os.path.basename(self.__local_path_to_scan_script)
            if os.path.basename(self.__remote_path_to_scan_script) != script_name:
                self.__remote_path_to_scan_script = posixpath.join(self.__remote_path_to_scan_script, script_name)

    @property
    def path_pair_id(self) -> Optional[str]:
        return self.__path_pair_id

    @property
    def path_pair_name(self) -> Optional[str]:
        return self.__path_pair_name

    def export_recycled_state(self) -> tuple[bool, str]:
        """Return the small mutable state that must survive one-shot workers."""
        return self.__first_run, self.__remote_path_to_scan_script

    def __getstate__(self) -> dict[str, object]:
        state = self.__dict__.copy()
        state["_RemoteScanner__performance_diagnostics"] = None
        return state

    def apply_recycled_state(self, state: object) -> None:
        if not isinstance(state, tuple) or len(state) != 2 or \
                type(state[0]) is not bool or not isinstance(state[1], str):
            raise TypeError("Invalid recycled remote scanner state")
        self.__first_run = state[0]
        self.__remote_path_to_scan_script = state[1]

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("RemoteScanner")
        self.__ssh.set_base_logger(self.logger)

    def set_performance_diagnostics(self, diagnostics: object) -> None:
        self.__performance_diagnostics = diagnostics
        self.__ssh.set_performance_diagnostics(diagnostics)

    def __begin_duration(self, metric: str) -> object:
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return None
        try:
            return diagnostics.begin_duration(metric)
        except Exception:
            return None

    def __finish_duration(self, metric: str, started_at: object) -> None:
        if started_at is None:
            return
        diagnostics = self.__performance_diagnostics
        if diagnostics is None:
            return
        try:
            diagnostics.finish_duration(metric, started_at)
        except Exception:
            pass

    def __publish_progress(self, *args: object) -> None:
        callback = self.__progress_callback
        if callback is None:
            return
        started_at = self.__begin_duration(DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION)
        try:
            callback(*args)
        finally:
            self.__finish_duration(DURATION_REMOTE_SCAN_PROGRESS_PUBLICATION, started_at)

    def __flush_pending_root_progress(self, state: dict[str, object]) -> None:
        """Publish completed framed roots in wire order and clear the batch."""
        pending_roots = cast(List[SystemFile], state["pending_roots"])
        if not pending_roots:
            return
        batch = list(pending_roots)
        pending_roots.clear()
        state["pending_roots_started_at"] = None
        self.__publish_progress(batch, self.__path_pair_id, self.__path_pair_name, None, False)

    def __flush_pending_unchanged_progress(self, state: dict[str, object]) -> None:
        """Publish compact retained-root evidence without materializing trees."""
        pending = cast(dict[str, str], state["pending_unchanged_root_fingerprints"])
        if not pending:
            return
        fingerprints = dict(pending)
        pending.clear()
        self.__publish_progress([], self.__path_pair_id, self.__path_pair_name, None, False, fingerprints)

    def set_accepted_root_fingerprints(self, fingerprints: object) -> None:
        """Set model-owned accepted hints for one future stream generation."""
        if isinstance(fingerprints, dict) and isinstance(fingerprints.get(self.__path_pair_id), dict):
            fingerprints = fingerprints[self.__path_pair_id]
        if not isinstance(fingerprints, dict):
            self.__accepted_root_fingerprints = {}
            return
        self.__accepted_root_fingerprints = {
            name: fingerprint for name, fingerprint in fingerprints.items()
            if isinstance(name, str) and isinstance(fingerprint, str) and
            re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        }

    @overrides(IScanner)
    def set_progress_callback(self, callback: Optional[ScanProgressCallback]) -> None:
        self.__progress_callback = callback
        self.__progress_callback_supports_fingerprints = False
        if callback is None:
            return
        try:
            inspect.signature(callback).bind([], None, None, None, False, {})
            self.__progress_callback_supports_fingerprints = True
        except (TypeError, ValueError):
            # A legacy five-argument callback remains supported by using the
            # established full-tree stream instead of emitting markers it
            # cannot carry to the accumulator.
            pass

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        if self.__remote_scan_lease is None:
            return self.__scan()
        with self.__remote_scan_lease.hold():
            return self.__scan()

    def __scan(self) -> List[SystemFile]:
        if not self.__is_valid_remote_script_path(self.__remote_path_to_scan_script):
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(
                    "Remote scan script path must be absolute or start with '~': {}".format(
                        self.__remote_path_to_scan_script
                    )
                ),
                recoverable=False
            )
        if self.__first_run:
            self._install_scanfs()

        try:
            remote_scanfs_path = escape_remote_path_for_shell(
                self.__remote_path_to_scan_script,
                allow_tilde_expansion=True
            )
            remote_scan_path = escape_remote_path_for_shell(
                self.__remote_path_to_scan,
                allow_tilde_expansion=True
            )
            # Run packaged binaries or non-Python shebang helpers directly; Python shebang
            # helpers keep the configured interpreter path.
            stream_args = " --stream --stream-batch-size 64" if self.__progress_callback is not None else ""
            if stream_args and self.__progress_callback_supports_fingerprints and \
                    self.__accepted_root_fingerprints:
                fingerprint_json = json.dumps(
                    self.__accepted_root_fingerprints, sort_keys=True, separators=(",", ":")
                )
                # A hint must never make a valid scan exceed shell argument
                # bounds.  Omit it as a safe full-tree fallback instead.
                fingerprint_bytes = fingerprint_json.encode("utf-8")
                if len(fingerprint_bytes) <= self._MAX_KNOWN_ROOT_FINGERPRINT_BYTES:
                    encoded_fingerprints = base64.urlsafe_b64encode(fingerprint_bytes).decode("ascii")
                    stream_args += " --stream-known-root-fingerprints-b64 {}".format(
                        shlex.quote(encoded_fingerprints)
                    )
            if self.__should_execute_scanfs_directly(self.__local_path_to_scan_script):
                command = "{}{} {}".format(remote_scanfs_path, stream_args, remote_scan_path)
                legacy_command = "{} {}".format(remote_scanfs_path, remote_scan_path)
            else:
                remote_python_path = shlex.quote(self._normalize_remote_python_path(self.__remote_python_path))
                command = "{} {} {}".format(
                    remote_python_path,
                    remote_scanfs_path,
                    stream_args.lstrip() + " " + remote_scan_path if stream_args else remote_scan_path
                )
                legacy_command = "{} {} {}".format(
                    remote_python_path,
                    remote_scanfs_path,
                    remote_scan_path,
                )
            stream_shell = getattr(self.__ssh, "shell_stream", None) \
                if self.__progress_callback is not None else None
            def new_stream_state() -> dict[str, object]:
                return {
                    "manifest": False,
                    "complete": False,
                    "saw_prefix": False,
                    "defer_complete": True,
                    "manifest_names": set(),
                    "manifest_expected": None,
                    "manifest_collecting": False,
                    "current_root": None,
                    "next_root_id": 0,
                    "emitted_root_names": set(),
                    "pending_roots": [],
                    "pending_roots_started_at": None,
                    "pending_unchanged_root_fingerprints": {},
                    "dialect": None,
                }

            stream_state = new_stream_state()
            stream_buffer = ""
            stream_decoder = codecs.getincrementaldecoder("utf-8")()
            stream_files: List[SystemFile] = []
            stream_error: List[Exception] = []
            stream_protocol_mode = "unknown"
            stream_probe = bytearray()
            legacy_output = bytearray()
            stream_prefix = b"SEEDSYNC_SCAN_V2\t"
            stream_probe_limit = 64 * 1024
            stream_line_whitespace = b" \t\r\v\f"
            stream_json_whitespace = b" \t\r\n\v\f"

            def looks_like_legacy_json(candidate: bytes, line_complete: bool) -> bool:
                candidate = candidate.lstrip(stream_json_whitespace)
                if candidate.startswith(b"["):
                    first_item = candidate[1:].lstrip(stream_json_whitespace)
                    if not first_item:
                        return line_complete
                    return first_item[:1] in b"[{\"-0123456789tfn]"
                if candidate.startswith(b"{"):
                    first_item = candidate[1:].lstrip(stream_json_whitespace)
                    if not first_item:
                        return line_complete
                    return first_item[:1] in b'\"}'
                return False

            def classify_stream_probe() -> None:
                """Find a V2 record or a JSON aggregate at a line boundary."""
                nonlocal stream_protocol_mode
                if stream_protocol_mode != "unknown":
                    return

                offset = 0
                while True:
                    line_end = stream_probe.find(b"\n", offset)
                    if line_end < 0:
                        break
                    line_end += 1
                    line = bytes(stream_probe[offset:line_end])
                    candidate = line.lstrip(stream_line_whitespace)
                    if candidate.startswith(stream_prefix):
                        stream_protocol_mode = "v2"
                        stream_probe.clear()
                        return
                    if looks_like_legacy_json(candidate, line_complete=True):
                        stream_protocol_mode = "legacy"
                        json_offset = offset + len(line) - len(candidate)
                        legacy_output.extend(stream_probe[json_offset:])
                        stream_probe.clear()
                        return
                    offset = line_end

                tail = bytes(stream_probe[offset:])
                candidate = tail.lstrip(stream_line_whitespace)
                if candidate.startswith(stream_prefix):
                    stream_protocol_mode = "v2"
                    stream_probe.clear()
                elif looks_like_legacy_json(candidate, line_complete=False):
                    stream_protocol_mode = "legacy"
                    json_offset = offset + len(tail) - len(candidate)
                    legacy_output.extend(stream_probe[json_offset:])
                    stream_probe.clear()

            def retain_legacy_chunk(chunk: object) -> bytes:
                """Keep aggregate bytes only until the stream protocol is known.

                ``Sshcp.shell_stream(..., retain_output=False)`` deliberately
                returns no aggregate bytes.  Legacy helpers can nevertheless
                accept ``--stream`` and send their one-shot JSON list through
                the callback, so retain those chunks for the existing JSON
                fallback.  Once the V2 prefix is identified, discard the probe
                and never retain the raw V2 stream.
                """
                nonlocal stream_protocol_mode
                if isinstance(chunk, bytes):
                    raw_chunk = chunk
                elif isinstance(chunk, str):
                    raw_chunk = chunk.encode("utf-8")
                else:
                    raw_chunk = str(chunk).encode("utf-8")
                if not raw_chunk or stream_protocol_mode == "v2":
                    return raw_chunk
                if stream_protocol_mode == "legacy":
                    legacy_output.extend(raw_chunk)
                    return raw_chunk

                probe_room = stream_probe_limit - len(stream_probe)
                if len(raw_chunk) > probe_room:
                    if probe_room > 0:
                        stream_probe.extend(raw_chunk[:probe_room])
                        classify_stream_probe()
                    if stream_protocol_mode == "unknown":
                        stream_error.append(ValueError("scan stream protocol probe exceeded limit"))
                        return raw_chunk
                    if stream_protocol_mode == "legacy":
                        legacy_output.extend(raw_chunk[probe_room:])
                    return raw_chunk
                stream_probe.extend(raw_chunk)
                classify_stream_probe()
                return raw_chunk

            def validate_v2_chunk_size(raw_chunk: bytes) -> None:
                """Reject an oversized encoded V2 line before buffering it."""
                # The incremental UTF-8 decoder can retain up to three raw
                # bytes of a split codepoint. Include those bytes: they are
                # part of the same wire line even though not in stream_buffer.
                pending_bytes, _ = stream_decoder.getstate()
                partial_size = len(stream_buffer.encode("utf-8")) + len(pending_bytes)
                chunks = raw_chunk.split(b"\n")
                for index, encoded_line in enumerate(chunks):
                    line_size = partial_size + len(encoded_line)
                    if index < len(chunks) - 1:
                        line_size += 1
                    self._validate_v2_stream_record_size(line_size)
                    partial_size = 0 if index < len(chunks) - 1 else line_size

            def consume_stream_chunk(chunk: object) -> None:
                nonlocal stream_buffer
                if stream_error:
                    return
                parsing_started = self.__begin_duration(DURATION_REMOTE_SCAN_STREAM_PARSING)
                try:
                    raw_chunk = retain_legacy_chunk(chunk)
                    if stream_protocol_mode == "v2":
                        validate_v2_chunk_size(raw_chunk)
                    if isinstance(chunk, bytes):
                        stream_buffer += stream_decoder.decode(chunk, final=False)
                    else:
                        stream_buffer += str(chunk)
                    lines = stream_buffer.split("\n")
                    stream_buffer = lines.pop()
                    for line in lines:
                        self.__decode_stream_record(
                            line.rstrip("\r"),
                            stream_files,
                            stream_state
                        )
                except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as err:
                    stream_error.append(err)
                finally:
                    self.__finish_duration(DURATION_REMOTE_SCAN_STREAM_PARSING, parsing_started)

            def invoke_stream(command_to_run: str, callback, retain_output: bool) -> bytes:
                implementation = getattr(stream_shell, "side_effect", None)
                if callable(implementation):
                    try:
                        inspect.signature(implementation).bind(
                            command_to_run,
                            callback,
                            retain_output=retain_output,
                        )
                    except (TypeError, ValueError):
                        return stream_shell(command_to_run, callback)
                try:
                    return stream_shell(command_to_run, callback, retain_output=retain_output)
                except TypeError as err:
                    # Preserve compatibility with custom/test transports that
                    # still expose the original two-argument callback API.
                    if "retain_output" not in str(err):
                        raise
                    return stream_shell(command_to_run, callback)

            if callable(stream_shell):
                try:
                    out = invoke_stream(command, consume_stream_chunk, retain_output=False)
                except SshcpError as stream_error_exception:
                    if not self.__is_unsupported_stream_option_error(stream_error_exception):
                        raise
                    # A clearly identified legacy helper rejection gets one
                    # aggregate retry; never retry per file/root.
                    stream_buffer = ""
                    stream_files.clear()
                    stream_state = new_stream_state()
                    stream_error.clear()
                    stream_decoder = codecs.getincrementaldecoder("utf-8")()
                    stream_protocol_mode = "unknown"
                    stream_probe.clear()
                    legacy_output.clear()
                    out = invoke_stream(legacy_command, lambda chunk: None, retain_output=True)
                if stream_protocol_mode == "unknown":
                    stream_protocol_mode = "legacy"
                    legacy_output.extend(stream_probe)
                    stream_probe.clear()
                if not stream_error:
                    try:
                        stream_buffer += stream_decoder.decode(b"", final=True)
                    except UnicodeError as err:
                        stream_error.append(err)
                if stream_buffer and not stream_error:
                    consume_stream_chunk("\n")
                if stream_error:
                    raise stream_error[0]
                if stream_state["saw_prefix"]:
                    if not stream_state["manifest"] or not stream_state["complete"]:
                        raise TypeError("incomplete scan stream")
                    remote_files = stream_files
                else:
                    # Older scanfs helpers return one JSON list. Decode it
                    # after EOF while keeping the one-command stream path.
                    # The production shell transport returns b"" when raw
                    # retention is disabled, so use the callback-captured
                    # bytes for legacy helpers that accept --stream but still
                    # emit aggregate JSON.
                    aggregate = bytes(legacy_output) or out
                    decoded: object = json.loads(aggregate.decode("utf-8"))
                    if not isinstance(decoded, list):
                        raise TypeError("scan data must be a list")
                    decode_system_file = cast(
                        Callable[[dict[str, object]], SystemFile],
                        getattr(SystemFile, "from_dict"),
                    )
                    remote_files = []
                    for item in cast(List[object], decoded):
                        if not isinstance(item, dict):
                            raise TypeError("scan entries must be objects")
                        remote_files.append(decode_system_file(cast(dict[str, object], item)))
                    self.__publish_progress(
                        [], self.__path_pair_id, self.__path_pair_name,
                        {file.name for file in remote_files}, False
                    )
                    self.__publish_progress(
                        remote_files, self.__path_pair_id, self.__path_pair_name,
                        None, False
                    )
                    self.__publish_progress(
                        [], self.__path_pair_id, self.__path_pair_name,
                        None, True
                    )
                if stream_state["saw_prefix"]:
                    # Protocol completion is provisional until the transport
                    # has returned successfully and validated its exit status.
                    self.__flush_pending_root_progress(stream_state)
                    self.__flush_pending_unchanged_progress(stream_state)
                    self.__publish_progress(
                        [], self.__path_pair_id, self.__path_pair_name,
                        None, True
                    )
            else:
                out = self.__ssh.shell(command)
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as err:
            self.logger.error("JSON decode error while streaming scan: {}".format(str(err)))
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_SCAN.format("Invalid scan data"),
                recoverable=self.__progress_callback is not None
            )
        except SshcpError as e:
            self.logger.warning("Caught an SshcpError: {}".format(str(e)))
            recoverable = True
            # Any scanner errors are fatal
            if "SystemScannerError" in str(e):
                recoverable = False
            # First run errors are only recoverable for transient SSH issues.
            # Non-transient first-run errors should still prompt user correction.
            if self.__first_run and not self._is_transient_ssh_error(e):
                recoverable = False
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_SCAN.format(str(e).strip()),
                recoverable=recoverable
            )

        try:
            if callable(stream_shell):
                # The streaming callback already decoded and published every
                # record before the SSH process reached EOF.
                pass
            elif self.__progress_callback is not None and b"SEEDSYNC_SCAN_V2\t" in out:
                parsing_started = self.__begin_duration(DURATION_REMOTE_SCAN_STREAM_PARSING)
                try:
                    remote_files = self.__decode_stream(out)
                finally:
                    self.__finish_duration(DURATION_REMOTE_SCAN_STREAM_PARSING, parsing_started)
            else:
                out_str = out.decode("utf-8")
                decoded: object = json.loads(out_str)
                if not isinstance(decoded, list):
                    raise TypeError("scan data must be a list")
                remote_files = []
                for item in cast(List[object], decoded):
                    if not isinstance(item, dict):
                        raise TypeError("scan entries must be objects")
                    decode_system_file = cast(
                        Callable[[dict[str, object]], SystemFile],
                        getattr(SystemFile, "from_dict"),
                    )
                    remote_files.append(decode_system_file(cast(dict[str, object], item)))
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as err:
            self.logger.error("JSON decode error: {}\n{}".format(str(err), out))
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_SCAN.format("Invalid scan data"),
                # A streamed generation may already have published a manifest
                # or root batches.  Keep the last-good model and retry instead
                # of turning a malformed/partial WAN response into a fatal
                # coordinator error.  Legacy one-shot callers retain the old
                # non-recoverable contract.
                recoverable=self.__progress_callback is not None
            )

        self.__first_run = False
        return remote_files

    def __decode_stream_record(self,
                               line: str,
                               remote_files: List[SystemFile],
                               state: dict[str, object]) -> None:
        assert self.__progress_callback is not None
        prefix = "SEEDSYNC_SCAN_V2\t"
        if not line.startswith(prefix):
            return
        state["saw_prefix"] = True
        record = json.loads(line[len(prefix):])
        if not isinstance(record, dict) or not isinstance(record.get("type"), str):
            raise TypeError("invalid scan stream record")
        record_type = record["type"]
        if state["complete"]:
            raise TypeError("scan stream record after complete")
        if record_type == "manifest":
            names = record.get("names")
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                raise TypeError("invalid scan manifest")
            if state["dialect"] not in (None, "legacy") or state["manifest"] or \
                    state["manifest_collecting"] or len(set(names)) != len(names):
                raise TypeError("duplicate or unexpected scan manifest")
            state["dialect"] = "legacy"
            state["manifest"] = True
            manifest_names = cast(set[str], state["manifest_names"])
            manifest_names.update(names)
            self.__publish_progress([], self.__path_pair_id, self.__path_pair_name, manifest_names, False)
        elif record_type == "manifest_begin":
            count = record.get("count")
            if type(count) is not int or count < 0 or state["dialect"] not in (None, "framed") or \
                    state["manifest"] or state["manifest_collecting"]:
                raise TypeError("invalid scan manifest start")
            state["dialect"] = "framed"
            state["manifest_collecting"] = True
            state["manifest_expected"] = count
        elif record_type == "manifest_names":
            names = record.get("names")
            if state["dialect"] != "framed" or not state["manifest_collecting"] or not isinstance(names, list) or \
                    not all(isinstance(name, str) for name in names):
                raise TypeError("invalid scan manifest fragment")
            manifest_names = cast(set[str], state["manifest_names"])
            if not names or len(set(names)) != len(names) or any(name in manifest_names for name in names):
                raise TypeError("duplicate or empty scan manifest fragment")
            manifest_names.update(names)
        elif record_type == "manifest_end":
            manifest_names = cast(set[str], state["manifest_names"])
            if state["dialect"] != "framed" or not state["manifest_collecting"] or \
                    state["manifest_expected"] != len(manifest_names):
                raise TypeError("incomplete scan manifest")
            state["manifest_collecting"] = False
            state["manifest"] = True
            self.__publish_progress([], self.__path_pair_id, self.__path_pair_name, manifest_names, False)
        elif record_type == "roots":
            data = record.get("files")
            if state["dialect"] != "legacy" or not state["manifest"] or \
                    state["manifest_collecting"] or not isinstance(data, list):
                raise TypeError("invalid scan root batch")
            decode_system_file = cast(Callable[[dict[str, object]], SystemFile], getattr(SystemFile, "from_dict"))
            batch: List[SystemFile] = []
            for item in data:
                if not isinstance(item, dict):
                    raise TypeError("invalid scan root")
                batch.append(decode_system_file(cast(dict[str, object], item)))
            manifest_names = cast(set[str], state["manifest_names"])
            emitted_root_names = cast(set[str], state["emitted_root_names"])
            batch_names = [file.name for file in batch]
            if len(set(batch_names)) != len(batch_names) or any(
                    name not in manifest_names or name in emitted_root_names for name in batch_names):
                raise TypeError("invalid or duplicate scan root")
            remote_files.extend(batch)
            emitted_root_names.update(batch_names)
            self.__publish_progress(batch, self.__path_pair_id, self.__path_pair_name, None, False)
        elif record_type == "root_begin":
            root_id = record.get("id")
            name = record.get("name")
            if state["dialect"] != "framed" or not state["manifest"] or state["manifest_collecting"] or \
                    state["current_root"] is not None or \
                    type(root_id) is not int or root_id != state["next_root_id"] or not isinstance(name, str) or \
                    name not in cast(set[str], state["manifest_names"]) or \
                    name in cast(set[str], state["emitted_root_names"]):
                raise TypeError("invalid scan root start")
            state["current_root"] = {
                "id": root_id,
                "name": name,
                "next_node_id": 0,
                "node_count": 0,
                "root": None,
                "stack": [],
            }
        elif record_type == "root_node":
            self.__decode_framed_root_node(
                state, record.get("root"), record.get("id"), record.get("parent"), record.get("file")
            )
        elif record_type == "root_nodes":
            root_id = record.get("root")
            nodes = record.get("nodes")
            if type(root_id) is not int or not isinstance(nodes, list) or not nodes or \
                    len(nodes) > self._MAX_V2_ROOT_NODES_PER_RECORD:
                raise TypeError("invalid scan root node batch")
            for node_record in nodes:
                if not isinstance(node_record, dict):
                    raise TypeError("invalid scan root node")
                self.__decode_framed_root_node(
                    state,
                    root_id,
                    node_record.get("id"),
                    node_record.get("parent"),
                    node_record.get("file"),
                )
        elif record_type == "root_end":
            current_root = state["current_root"]
            root_id = record.get("id")
            node_count = record.get("nodes")
            if state["dialect"] != "framed" or not isinstance(current_root, dict) or root_id != current_root["id"] or \
                    type(node_count) is not int or node_count != current_root["node_count"] or \
                    current_root["root"] is None:
                raise TypeError("incomplete scan root")
            root = cast(SystemFile, current_root["root"])
            remote_files.append(root)
            completed_at = time.monotonic()
            pending_roots = cast(List[SystemFile], state["pending_roots"])
            if not pending_roots:
                state["pending_roots_started_at"] = completed_at
            pending_roots.append(root)
            pending_started_at = cast(Optional[float], state["pending_roots_started_at"])
            if len(pending_roots) >= self._ROOT_PROGRESS_BATCH_SIZE or (
                    pending_started_at is not None and
                    completed_at - pending_started_at >= self._ROOT_PROGRESS_MAX_AGE_SECONDS):
                self.__flush_pending_root_progress(state)
            cast(set[str], state["emitted_root_names"]).add(root.name)
            state["next_root_id"] = cast(int, state["next_root_id"]) + 1
            state["current_root"] = None
        elif record_type == "root_unchanged":
            name = record.get("name")
            fingerprint = record.get("fingerprint")
            if state["dialect"] != "framed" or not state["manifest"] or state["manifest_collecting"] or \
                    state["current_root"] is not None or not isinstance(name, str) or \
                    not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or \
                    name not in cast(set[str], state["manifest_names"]) or \
                    name in cast(set[str], state["emitted_root_names"]):
                raise TypeError("invalid unchanged scan root")
            cast(set[str], state["emitted_root_names"]).add(name)
            pending = cast(dict[str, str], state["pending_unchanged_root_fingerprints"])
            pending[name] = fingerprint
        elif record_type == "complete":
            if not state["manifest"] or state["manifest_collecting"] or state["current_root"] is not None:
                raise TypeError("incomplete scan stream")
            state["complete"] = True
            if not state.get("defer_complete", False):
                self.__flush_pending_root_progress(state)
                self.__flush_pending_unchanged_progress(state)
                self.__publish_progress([], self.__path_pair_id, self.__path_pair_name, None, True)
        else:
            raise TypeError("unknown scan stream record")

    def __decode_framed_root_node(self,
                                  state: dict[str, object],
                                  root_id: object,
                                  node_id: object,
                                  parent_id: object,
                                  data: object) -> None:
        """Apply one framed node from either legacy or batched V2 records."""
        current_root = state["current_root"]
        if state["dialect"] != "framed" or not isinstance(current_root, dict):
            raise TypeError("scan node without a root")
        if root_id != current_root["id"] or type(node_id) is not int or \
                node_id != current_root["next_node_id"] or \
                (parent_id is not None and type(parent_id) is not int) or \
                not isinstance(data, dict) or "children" in data:
            raise TypeError("invalid scan root node")
        decode_system_file = cast(Callable[[dict[str, object]], SystemFile], getattr(SystemFile, "from_dict"))
        node = decode_system_file(cast(dict[str, object], data))
        stack = cast(list[tuple[int, SystemFile]], current_root["stack"])
        if not stack:
            if parent_id is not None or node.name != current_root["name"]:
                raise TypeError("invalid scan root node ordering")
            current_root["root"] = node
        else:
            parent_index = next((index for index, item in enumerate(stack) if item[0] == parent_id), None)
            if parent_index is None:
                raise TypeError("invalid scan node parent")
            parent = stack[parent_index][1]
            parent.add_child(node)
            del stack[parent_index + 1:]
        stack.append((node_id, node))
        current_root["next_node_id"] = node_id + 1
        current_root["node_count"] = cast(int, current_root["node_count"]) + 1

    def __decode_stream(self, output: bytes) -> List[SystemFile]:
        assert self.__progress_callback is not None
        remote_files: List[SystemFile] = []
        state: dict[str, object] = {
            "manifest": False,
            "complete": False,
            "saw_prefix": False,
            # One-shot V2 output is validated record-by-record below. Do not
            # make it authoritative until that full payload has passed.
            "defer_complete": True,
            "manifest_names": set(),
            "manifest_expected": None,
            "manifest_collecting": False,
            "current_root": None,
            "next_root_id": 0,
            "emitted_root_names": set(),
            "pending_roots": [],
            "pending_roots_started_at": None,
            "pending_unchanged_root_fingerprints": {},
            "dialect": None,
        }
        for raw_line in output.splitlines(keepends=True):
            is_v2_record = raw_line.startswith(b"SEEDSYNC_SCAN_V2\t")
            if is_v2_record or state["saw_prefix"]:
                self._validate_v2_stream_record_bytes(raw_line)
            if state["saw_prefix"] and not is_v2_record:
                raise TypeError("non-protocol output after scan stream start")
            line = raw_line.decode("utf-8").rstrip("\r\n")
            self.__decode_stream_record(line, remote_files, state)
        if not state["manifest"] or not state["complete"]:
            raise TypeError("incomplete scan stream")
        self.__flush_pending_root_progress(state)
        self.__flush_pending_unchanged_progress(state)
        self.__publish_progress([], self.__path_pair_id, self.__path_pair_name, None, True)
        return remote_files

    def __check_remote_scanfs_target(self, remote_path: str) -> str:
        remote_path_for_shell = escape_remote_path_for_shell(remote_path, allow_tilde_expansion=True)
        return self.__ssh.shell(
            "if [ -d {} ]; then echo IS_DIRECTORY; else md5sum {} | awk '{{print $1}}' || echo; fi".format(
                remote_path_for_shell,
                remote_path_for_shell
            )
        ).decode().strip()

    def __raise_remote_scanfs_directory_error(self,
                                              remote_path: str,
                                              path_label: str = "Server Script Path",
                                              original_error: Optional[str] = None):
        message = (
            "{} '{}' is a directory on the remote server. Change the 'Server Script Path' setting "
            "to a writable location outside your sync tree (e.g. '~' or '~/.local') and remove "
            "the conflicting directory from the remote server."
        ).format(path_label, remote_path)
        if original_error:
            message = "{} Original error: {}".format(message, original_error.strip())
        raise ScannerError(
            Localization.Error.REMOTE_SERVER_INSTALL.format(message),
            recoverable=False
        )

    def _install_scanfs(self):
        # Check md5sum on remote to see if we can skip installation
        if not self.__is_valid_local_script_path(self.__local_path_to_scan_script):
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_SCAN.format(
                    "Failed to find scanfs executable at {}".format(self.__local_path_to_scan_script)
                ),
                recoverable=False
            )
        try:
            self.__ssh.detect_shell()
        except SshcpError as e:
            self.logger.exception("Shell detection failed")
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(str(e).strip()),
                recoverable=self.__first_run and self._is_transient_ssh_error(e)
            )
        with open(self.__local_path_to_scan_script, "rb") as f:
            local_md5sum = hashlib.md5(f.read()).hexdigest()
        self.logger.debug("Local scanfs md5sum = {}".format(local_md5sum))
        try:
            remote_target_state = self.__check_remote_scanfs_target(self.__remote_path_to_scan_script)
        except SshcpError as e:
            self.logger.exception("Caught scp exception")
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(str(e).strip()),
                recoverable=self.__first_run and self._is_transient_ssh_error(e)
            )
        if remote_target_state == "IS_DIRECTORY":
            self.__raise_remote_scanfs_directory_error(self.__remote_path_to_scan_script)
        if remote_target_state == local_md5sum:
            self.logger.info("Skipping remote scanfs installation: already installed")
            return

        # Go ahead and install
        self.logger.info("Installing local:{} to remote:{}".format(
            self.__local_path_to_scan_script,
            self.__remote_path_to_scan_script
        ))
        if not os.path.isfile(self.__local_path_to_scan_script):
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_SCAN.format(
                    "Failed to find scanfs executable at {}".format(self.__local_path_to_scan_script)
                ),
                recoverable=False
            )
        try:
            self.__ssh.copy(local_path=self.__local_path_to_scan_script,
                            remote_path=self.__remote_path_to_scan_script)
        except SshcpError as e:
            if self._is_scanfs_destination_write_denied(e, self.__remote_path_to_scan_script):
                self._install_scanfs_with_home_fallback(local_md5sum, str(e))
                return
            self.logger.exception("Caught scp exception")
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(str(e).strip()),
                recoverable=self.__first_run and self._is_transient_ssh_error(e)
            )

    def _install_scanfs_with_home_fallback(self, local_md5sum: str, original_error: str):
        script_name = os.path.basename(self.__local_path_to_scan_script)
        fallback_path = posixpath.join("~", script_name)

        self.logger.warning(
            "Script path '{}' is not writable ({}). Retrying with home directory: '{}'".format(
                self.__remote_path_to_scan_script,
                original_error.strip(),
                fallback_path
            )
        )
        self.logger.warning(
            "Update 'Server Script Path' in Settings to '~' to avoid this fallback on restart."
        )

        fallback_target_state = None
        try:
            fallback_target_state = self.__check_remote_scanfs_target(fallback_path)
        except SshcpError as e:
            self.logger.warning("Could not check fallback path type: {}".format(str(e)))

        if fallback_target_state == "IS_DIRECTORY":
            self.__raise_remote_scanfs_directory_error(
                fallback_path,
                path_label="Fallback scanfs path",
                original_error=original_error
            )
        if fallback_target_state == local_md5sum:
            self.logger.info("Skipping fallback remote scanfs installation: already installed")
            self.__remote_path_to_scan_script = fallback_path
            return

        self.logger.info("Installing local:{} to remote:{}".format(
            self.__local_path_to_scan_script,
            fallback_path
        ))
        try:
            self.__ssh.copy(local_path=self.__local_path_to_scan_script,
                            remote_path=fallback_path)
        except SshcpError as fallback_e:
            self.logger.exception("Caught scp exception")
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(
                    "Could not install scanner to '{}' ({}), fallback to '{}' also failed: {}".format(
                        self.__remote_path_to_scan_script,
                        original_error.strip(),
                        fallback_path,
                        str(fallback_e).strip()
                    )
                ),
                recoverable=self.__first_run and self._is_transient_ssh_error(fallback_e)
            )

        self.__remote_path_to_scan_script = fallback_path

    @staticmethod
    def __is_valid_remote_script_path(path: object) -> bool:
        return bool(isinstance(path, str) and path.strip() and (
            posixpath.isabs(path) or RemoteScanner._SAFE_TILDE_PREFIX.match(path) is not None
        ))

    @staticmethod
    def __is_valid_local_script_path(path: object) -> bool:
        return bool(isinstance(path, str) and path.strip())
