# Copyright 2026, SeedSync Contributors, All rights reserved.

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from transfer.factory import create_transfer_backend


class TestTransferFactory(unittest.TestCase):
    def _make_config(self, **overrides):
        config = SimpleNamespace(
            transfer_backend="lftp",
            remote_address="remote.server.com",
            remote_port=22,
            remote_username="user",
            remote_password="password",
            use_ssh_key=False,
            protocol="sftp",
            remote_ftp_port=21,
            ftp_ssl_verify_certificate=True,
            use_legacy_lftp_password_argv=False,
        )
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    @patch("transfer.factory.Lftp")
    def test_create_transfer_backend_uses_lftp_defaults(self, mock_lftp):
        config = self._make_config()

        create_transfer_backend(config, "password", "password")

        mock_lftp.assert_called_once_with(
            address="remote.server.com",
            port=22,
            user="user",
            password="password",
            protocol="sftp",
            remote_ftp_port=21,
            ssl_verify_certificate=True,
            use_legacy_lftp_password_argv=False,
        )

    @patch("transfer.factory.Lftp")
    def test_create_transfer_backend_uses_transfer_password_for_ftps(self, mock_lftp):
        config = self._make_config(protocol="ftps", use_ssh_key=True, remote_ftp_port=2121)

        create_transfer_backend(config, "transfer-password", None)

        mock_lftp.assert_called_once_with(
            address="remote.server.com",
            port=22,
            user="user",
            password="transfer-password",
            protocol="ftps",
            remote_ftp_port=2121,
            ssl_verify_certificate=True,
            use_legacy_lftp_password_argv=False,
        )

    @patch("transfer.factory.Lftp")
    def test_create_transfer_backend_propagates_file_only_legacy_password_argv_flag(self, mock_lftp):
        config = self._make_config(use_legacy_lftp_password_argv=True)

        create_transfer_backend(config, "password", "password")

        self.assertTrue(mock_lftp.call_args.kwargs["use_legacy_lftp_password_argv"])

    @patch("transfer.factory.RcloneTransferBackend")
    def test_create_transfer_backend_uses_rclone_backend_when_selected(self, mock_rclone_backend):
        config = self._make_config(transfer_backend="rclone", protocol="sftp")

        create_transfer_backend(config, "ignored-transfer-password", "ssh-password")

        mock_rclone_backend.assert_called_once_with(
            address="remote.server.com",
            port=22,
            user="user",
            password="ssh-password",
            use_ssh_key=False,
        )

    @patch("transfer.factory.RcloneTransferBackend")
    def test_create_transfer_backend_uses_none_password_for_rclone_key_auth(self, mock_rclone_backend):
        config = self._make_config(transfer_backend="rclone", use_ssh_key=True)

        create_transfer_backend(config, "ignored-transfer-password", None)

        mock_rclone_backend.assert_called_once_with(
            address="remote.server.com",
            port=22,
            user="user",
            password=None,
            use_ssh_key=True,
        )
