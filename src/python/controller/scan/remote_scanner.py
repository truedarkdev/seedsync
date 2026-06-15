# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import json
from typing import List
import os
import posixpath
from typing import Optional
import hashlib

from .scanner_process import IScanner, ScannerError
from common import overrides, Localization, escape_remote_path_for_shell
from ssh import Sshcp, SshcpError, TRANSIENT_ERROR_PATTERNS
from system import SystemFile


class RemoteScanner(IScanner):
    """
    Scanner implementation to scan the remote filesystem
    """

    @staticmethod
    def _is_transient_ssh_error(error: SshcpError) -> bool:
        error_message = str(error)
        return any(pattern in error_message for pattern in TRANSIENT_ERROR_PATTERNS)

    def __init__(self,
                 remote_address: str,
                 remote_username: str,
                 remote_password: Optional[str],
                 remote_port: int,
                 remote_path_to_scan: str,
                 local_path_to_scan_script: str,
                 remote_path_to_scan_script: str,
                 path_pair_id: str = None,
                 path_pair_name: str = None):
        self.logger = logging.getLogger("RemoteScanner")
        self.__remote_path_to_scan = remote_path_to_scan
        self.__local_path_to_scan_script = local_path_to_scan_script
        self.__remote_path_to_scan_script = remote_path_to_scan_script
        self.__ssh = Sshcp(host=remote_address,
                           port=remote_port,
                           user=remote_username,
                           password=remote_password)
        self.__first_run = True
        self.__path_pair_id = path_pair_id
        self.__path_pair_name = path_pair_name

        # Append scan script name to remote path if not there already
        if self.__is_valid_local_script_path(self.__local_path_to_scan_script) and \
                self.__is_valid_remote_script_path(self.__remote_path_to_scan_script):
            script_name = os.path.basename(self.__local_path_to_scan_script)
            if os.path.basename(self.__remote_path_to_scan_script) != script_name:
                self.__remote_path_to_scan_script = posixpath.join(self.__remote_path_to_scan_script, script_name)

    @property
    def path_pair_id(self) -> str:
        return self.__path_pair_id

    @property
    def path_pair_name(self) -> str:
        return self.__path_pair_name

    @overrides(IScanner)
    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("RemoteScanner")
        self.__ssh.set_base_logger(self.logger)

    @overrides(IScanner)
    def scan(self) -> List[SystemFile]:
        if not self.__is_valid_remote_script_path(self.__remote_path_to_scan_script):
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(
                    "Remote scan script path must be absolute: {}".format(self.__remote_path_to_scan_script)
                ),
                recoverable=False
            )
        if self.__first_run:
            self._install_scanfs()

        try:
            out = self.__ssh.shell("{} {}".format(
                escape_remote_path_for_shell(self.__remote_path_to_scan_script),
                escape_remote_path_for_shell(self.__remote_path_to_scan, allow_tilde_expansion=True)
            ))
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
            out_str = out.decode("utf-8") if isinstance(out, bytes) else out
            file_dicts = json.loads(out_str)
            remote_files = [SystemFile.from_dict(file_dict) for file_dict in file_dicts]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
            self.logger.error("JSON decode error: {}\n{}".format(str(err), out))
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_SCAN.format("Invalid scan data"),
                recoverable=False
            )

        self.__first_run = False
        return remote_files

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
                recoverable=False
            )
        with open(self.__local_path_to_scan_script, "rb") as f:
            local_md5sum = hashlib.md5(f.read()).hexdigest()
        self.logger.debug("Local scanfs md5sum = {}".format(local_md5sum))
        try:
            out = self.__ssh.shell("md5sum {} | awk '{{print $1}}' || echo".format(
                escape_remote_path_for_shell(self.__remote_path_to_scan_script)
            ))
            out = out.decode()
            if out == local_md5sum:
                self.logger.info("Skipping remote scanfs installation: already installed")
                return
        except SshcpError as e:
            self.logger.exception("Caught scp exception")
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(str(e).strip()),
                recoverable=self.__first_run and self._is_transient_ssh_error(e)
            )

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
            self.logger.exception("Caught scp exception")
            raise ScannerError(
                Localization.Error.REMOTE_SERVER_INSTALL.format(str(e).strip()),
                recoverable=self.__first_run and self._is_transient_ssh_error(e)
            )

    @staticmethod
    def __is_valid_remote_script_path(path) -> bool:
        return isinstance(path, str) and path.strip() and posixpath.isabs(path)

    @staticmethod
    def __is_valid_local_script_path(path) -> bool:
        return isinstance(path, str) and path.strip()
