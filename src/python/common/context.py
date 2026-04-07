# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import copy
import collections
from typing import Optional

# my libs
from .config import Config
from .breadcrumb_trace import BreadcrumbTraceCollector
from .path_pair import PathPairManager
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

    def print_to_log(self):
        # Print the config
        self.logger.debug("Config:")
        config_dict = self.config.as_dict()
        for section in config_dict.keys():
            for option in config_dict[section].keys():
                value = config_dict[section][option]
                if str(section).lower() == "general" and str(option).lower() in {"api_token", "webhook_secret"}:
                    value = "**REDACTED**"
                self.logger.debug("  {}.{}: {}".format(section, option, value))

        self.logger.debug("Args:")
        for name, value in self.args.as_dict().items():
            self.logger.debug("  {}: {}".format(name, value))
