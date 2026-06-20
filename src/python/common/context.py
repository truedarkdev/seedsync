# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import copy
import collections
from typing import Optional

# my libs
from .config import Config
from .breadcrumb_trace import BreadcrumbTraceCollector
from .path_pair import PathPair, PathPairManager
from .status import Status


class Args:
    """
    Container for args
    These are settings that aren't part of config but still needed by
    sub-components
    """
    def __init__(self):
        self.local_path_to_scanfs = None
        self.html_path = None
        self.debug = None
        self.exit = None
        self.web_bind_host = None

    def as_dict(self) -> dict:
        dct = collections.OrderedDict()
        dct["local_path_to_scanfs"] = str(self.local_path_to_scanfs)
        dct["html_path"] = str(self.html_path)
        dct["debug"] = str(self.debug)
        dct["exit"] = str(self.exit)
        dct["web_bind_host"] = str(self.web_bind_host)
        return dct


class Context:
    """
    Stores contextual information for the entire application
    """
    def __init__(self,
                 logger: logging.Logger,
                 web_access_logger: logging.Logger,
                 config: Config,
                 args: Args,
                 status: Status,
                 path_pair_manager: Optional[PathPairManager] = None,
                 breadcrumb_trace: Optional[BreadcrumbTraceCollector] = None):
        """
        Primary constructor to construct the top-level context
        """
        # Config
        self.logger = logger
        self.web_access_logger = web_access_logger
        self.config = config
        self.args = args
        self.status = status
        self.path_pair_manager = path_pair_manager
        self.breadcrumb_trace = breadcrumb_trace if breadcrumb_trace is not None else BreadcrumbTraceCollector(
            self.__breadcrumb_trace_enabled,
            max_entries=self.__breadcrumb_trace_retention_depth()
        )

    def create_child_context(self, context_name: str) -> "Context":
        child_context = copy.copy(self)
        child_context.logger = self.logger.getChild(context_name)
        return child_context

    def __breadcrumb_trace_enabled(self) -> bool:
        general_config = getattr(self.config, "general", None)
        if general_config is None:
            return False
        enabled = getattr(general_config, "breadcrumb_trace_enabled", False)
        return enabled if type(enabled) is bool else False

    def __breadcrumb_trace_retention_depth(self) -> int:
        general_config = getattr(self.config, "general", None)
        if general_config is None:
            return 128
        retention_depth = getattr(general_config, "breadcrumb_trace_retention_depth", 128)
        return retention_depth if type(retention_depth) is int and retention_depth > 0 else 128

    def __redact_config_log_value(self, section, option, value):
        section_name = str(section).lower()
        option_name = str(option).lower()
        if Config.is_sensitive_field(section_name, option_name):
            if section_name == "lftp" and option_name == "remote_password":
                return Config.REDACTED_SENTINEL if value else ""
            return Config.REDACTED_SENTINEL
        return value

    @staticmethod
    def __format_path_pair_log_identity(path_pair: PathPair) -> str:
        if path_pair.id:
            return "{} [{}]".format(path_pair.name, path_pair.id[:8])
        return path_pair.name

    def print_to_log(self):
        # Print the config
        self.logger.debug("Config:")
        config_dict = self.config.as_dict()
        for section in config_dict.keys():
            for option in config_dict[section].keys():
                value = config_dict[section][option]
                value = self.__redact_config_log_value(section, option, value)
                self.logger.debug("  {}.{}: {}".format(section, option, value))

        path_pairs = []
        if self.path_pair_manager is not None:
            path_pairs = list(self.path_pair_manager.get_all_pairs() or [])
        if path_pairs:
            self.logger.debug("Path Pairs:")
            for path_pair in path_pairs:
                enabled = "enabled" if path_pair.enabled else "disabled"
                auto_queue = "on" if path_pair.auto_queue else "off"
                self.logger.debug(
                    "  {}: {} -> {} ({}, auto_queue={})".format(
                        self.__format_path_pair_log_identity(path_pair),
                        path_pair.remote_path,
                        path_pair.local_path,
                        enabled,
                        auto_queue,
                    )
                )
        else:
            self.logger.debug("Path Pairs: (none)")

        self.logger.debug("Args:")
        for name, value in self.args.as_dict().items():
            self.logger.debug("  {}: {}".format(name, value))
