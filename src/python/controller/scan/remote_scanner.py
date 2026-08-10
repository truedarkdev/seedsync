# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import json
import codecs
import inspect
import re
from collections.abc import Callable
from typing import List, cast
import os
import posixpath
import shlex
from typing import Optional
import hashlib

from .scanner_process import IScanner, ScannerError, ScanProgressCallback
from common import overrides, Localization, escape_remote_path_for_shell
from ssh import Sshcp, SshcpError, TRANSIENT_ERROR_PATTERNS
from system import SystemFile


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
                 path_pair_name: Optional[str] = None):
        self.logger = logging.getLogger("RemoteScanner")
        self.__remote_path_to_scan = remote_path_to_scan
        self.__local_path_to_scan_script = local_path_to_scan_script
        self.__remote_path_to_scan_script = remote_path_to_scan_script
        self.__remote_python_path = remote_python_path
        self.__ssh = Sshcp(host=remote_address,
                           port=remote_port,
                           user=remote_username,
                           password=remote_password)
        self.__first_run = True
        self.__path_pair_id = path_pair_id
        self.__path_pair_name = path_pair_name
        self.__progress_callback: Optional[ScanProgressCallback] = None

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

    @overrides(IScanner)
    def set_progress_callback(self, callback: Optional[ScanProgressCallback]) -> None:
        self.__progress_callback = callback

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
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
            stream_args = " --stream --stream-batch-size 8" if self.__progress_callback is not None else ""
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
            stream_state = {
                "manifest": False,
                "complete": False,
                "saw_prefix": False,
                "defer_complete": True,
            }
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

            def retain_legacy_chunk(chunk: object) -> None:
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
                    return
                if stream_protocol_mode == "legacy":
                    legacy_output.extend(raw_chunk)
                    return

                probe_room = stream_probe_limit - len(stream_probe)
                if len(raw_chunk) > probe_room:
                    if probe_room > 0:
                        stream_probe.extend(raw_chunk[:probe_room])
                        classify_stream_probe()
                    if stream_protocol_mode == "unknown":
                        stream_error.append(ValueError("scan stream protocol probe exceeded limit"))
                        return
                    if stream_protocol_mode == "legacy":
                        legacy_output.extend(raw_chunk[probe_room:])
                    return
                stream_probe.extend(raw_chunk)
                classify_stream_probe()

            def consume_stream_chunk(chunk: object) -> None:
                nonlocal stream_buffer
                if stream_error:
                    return
                try:
                    retain_legacy_chunk(chunk)
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
                    stream_state.update({"manifest": False, "complete": False, "saw_prefix": False})
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
                    self.__progress_callback(
                        [], self.__path_pair_id, self.__path_pair_name,
                        {file.name for file in remote_files}, False
                    )
                    self.__progress_callback(
                        remote_files, self.__path_pair_id, self.__path_pair_name,
                        None, False
                    )
                    self.__progress_callback(
                        [], self.__path_pair_id, self.__path_pair_name,
                        None, True
                    )
                if stream_state["saw_prefix"]:
                    # Protocol completion is provisional until the transport
                    # has returned successfully and validated its exit status.
                    self.__progress_callback(
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
            out_str = out.decode("utf-8")
            if callable(stream_shell):
                # The streaming callback already decoded and published every
                # record before the SSH process reached EOF.
                pass
            elif self.__progress_callback is not None and "SEEDSYNC_SCAN_V2\t" in out_str:
                remote_files = self.__decode_stream(out_str)
            else:
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
                               state: dict[str, bool]) -> None:
        assert self.__progress_callback is not None
        prefix = "SEEDSYNC_SCAN_V2\t"
        if not line.startswith(prefix):
            return
        state["saw_prefix"] = True
        record = json.loads(line[len(prefix):])
        if not isinstance(record, dict) or not isinstance(record.get("type"), str):
            raise TypeError("invalid scan stream record")
        record_type = record["type"]
        if record_type == "manifest":
            names = record.get("names")
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                raise TypeError("invalid scan manifest")
            state["manifest"] = True
            self.__progress_callback([], self.__path_pair_id, self.__path_pair_name, set(names), False)
        elif record_type == "roots":
            data = record.get("files")
            if not isinstance(data, list):
                raise TypeError("invalid scan root batch")
            decode_system_file = cast(Callable[[dict[str, object]], SystemFile], getattr(SystemFile, "from_dict"))
            batch: List[SystemFile] = []
            for item in data:
                if not isinstance(item, dict):
                    raise TypeError("invalid scan root")
                batch.append(decode_system_file(cast(dict[str, object], item)))
            remote_files.extend(batch)
            self.__progress_callback(batch, self.__path_pair_id, self.__path_pair_name, None, False)
        elif record_type == "complete":
            state["complete"] = True
            if not state.get("defer_complete", False):
                self.__progress_callback([], self.__path_pair_id, self.__path_pair_name, None, True)
        else:
            raise TypeError("unknown scan stream record")

    def __decode_stream(self, output: str) -> List[SystemFile]:
        assert self.__progress_callback is not None
        remote_files: List[SystemFile] = []
        state = {"manifest": False, "complete": False, "saw_prefix": False, "defer_complete": False}
        for line in output.splitlines():
            self.__decode_stream_record(line, remote_files, state)
        if not state["manifest"] or not state["complete"]:
            raise TypeError("incomplete scan stream")
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
