# Copyright 2017, Inderpreet Singh, All rights reserved.

"""
Shared pytest fixtures for SeedSync tests.

These fixtures are optional. Existing unittest.TestCase tests continue to use
their setUp() methods, while new pytest-style tests can request fixtures by
name.
"""

import logging
import sys
from unittest.mock import MagicMock

import pytest

from common import Config


@pytest.fixture
def test_logger(request):
    """Return a logger configured for the current test node."""

    logger = logging.getLogger(request.node.name)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield logger
    logger.removeHandler(handler)


@pytest.fixture
def mock_context(test_logger):
    """Return a MagicMock context with commonly used config defaults."""

    context = MagicMock()
    context.logger = test_logger

    context.config.lftp.local_path = "/local/path"
    context.config.lftp.remote_address = "remote.server.com"
    context.config.lftp.remote_username = "user"
    context.config.lftp.remote_password = "password"
    context.config.lftp.use_ssh_key = False
    context.config.lftp.remote_port = 22
    context.config.lftp.remote_path = "/remote/path"
    context.config.lftp.remote_path_to_scan_script = "/usr/bin/scanfs"
    context.config.lftp.use_temp_file = False
    context.config.lftp.num_max_parallel_downloads = 2
    context.config.lftp.num_max_parallel_files_per_download = 3
    context.config.lftp.num_max_connections_per_root_file = 4
    context.config.lftp.num_max_connections_per_dir_file = 2
    context.config.lftp.num_max_total_connections = 8

    context.config.controller.interval_ms_downloading_scan = 500
    context.config.controller.interval_ms_local_scan = 30000
    context.config.controller.interval_ms_remote_scan = 30000
    context.config.controller.use_local_path_as_extract_path = True
    context.config.controller.extract_path = "/extract/path"

    context.config.general.verbose = False

    context.args.local_path_to_scanfs = "/local/bin/scanfs"

    return context


@pytest.fixture
def mock_context_with_real_config(test_logger):
    """Return a MagicMock context backed by a real Config instance."""

    context = MagicMock()
    context.config = Config()
    context.logger = test_logger
    return context
