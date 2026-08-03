# Copyright 2026, SeedSync Contributors, All rights reserved.

from typing import Any

from lftp import Lftp

from .rclone_backend import RcloneTransferBackend


def create_transfer_backend(config: Any, transfer_password: Any, ssh_password: Any):
    if getattr(config, "transfer_backend", "lftp") == "rclone":
        return RcloneTransferBackend(
            address=config.remote_address,
            port=config.remote_port,
            user=config.remote_username,
            password=ssh_password,
            use_ssh_key=getattr(config, "use_ssh_key", False),
        )
    return Lftp(
        address=config.remote_address,
        port=config.remote_port,
        user=config.remote_username,
        password=transfer_password if getattr(config, "protocol", "sftp") == "ftps" else ssh_password,
        protocol=config.protocol,
        remote_ftp_port=config.remote_ftp_port,
        ssl_verify_certificate=config.ftp_ssl_verify_certificate,
        use_legacy_lftp_password_argv=getattr(config, "use_legacy_lftp_password_argv", False),
    )
