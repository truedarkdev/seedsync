# Copyright 2017, Inderpreet Singh, All rights reserved.

LFTP_RUNTIME_RECONFIGURE_FIELDS = frozenset((
    "num_max_parallel_downloads",
    "num_max_parallel_files_per_download",
    "num_max_connections_per_root_file",
    "num_max_connections_per_dir_file",
    "num_max_total_connections",
    "rate_limit",
    "net_socket_buffer",
))

VALIDATE_RUNTIME_RECONFIGURE_FIELDS = frozenset((
    "xfer_verify",
))

GENERAL_RUNTIME_RECONFIGURE_FIELDS = frozenset((
    "verbose",
    "exclude_patterns",
))

GENERAL_RUNTIME_NO_RESTART_FIELDS = frozenset((
    "verbose",
    "exclude_patterns",
    "breadcrumb_trace_enabled",
))


def requires_restart(section: str, key: str) -> bool:
    if section == "general" and key in GENERAL_RUNTIME_NO_RESTART_FIELDS:
        return False
    if section == "lftp" and key in LFTP_RUNTIME_RECONFIGURE_FIELDS:
        return False
    if section == "validate" and key in VALIDATE_RUNTIME_RECONFIGURE_FIELDS:
        return False
    return True
