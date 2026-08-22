# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import json
import copy
import collections
from typing import Any, Mapping, Optional

# my libs
from .config import (
    Config,
    DEFAULT_BREADCRUMB_TRACE_MEMORY_BUDGET_BYTES,
    DEFAULT_BREADCRUMB_TRACE_MAX_ENTRIES,
    DEFAULT_BREADCRUMB_TRACE_POLICY,
    parse_breadcrumb_trace_policy,
)
from .breadcrumb_trace import BreadcrumbTraceCollector
from .performance_diagnostics import PerformanceDiagnosticsCollector
from .path_pair import PathPair, PathPairManager
from .status import Status


class Args:
    """
    Container for args
    These are settings that aren't part of config but still needed by
    sub-components
    """
    def __init__(self):
        self.local_path_to_scanfs: str | None = None
        self.html_path: str | None = None
        self.debug: bool | None = None
        self.exit: bool | None = None
        self.web_bind_host: str | None = None
        self.logdir: str | None = None
        self.history_log_path: str | None = None

    def as_dict(self) -> dict[str, str]:
        dct: collections.OrderedDict[str, str] = collections.OrderedDict()
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
                 breadcrumb_trace: Optional[BreadcrumbTraceCollector] = None,
                 performance_diagnostics: Optional[PerformanceDiagnosticsCollector] = None):
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
            max_entries=self.__breadcrumb_trace_max_entries(),
            memory_budget_bytes=self.__breadcrumb_trace_memory_budget_bytes(),
            policy=self.__breadcrumb_trace_policy(),
            policy_persist=self.__persist_breadcrumb_trace_policy,
        )
        self.performance_diagnostics = performance_diagnostics if performance_diagnostics is not None else \
            PerformanceDiagnosticsCollector(
                self.__performance_diagnostics_enabled,
                retention_depth=self.__performance_diagnostics_retention_depth(),
                sample_interval_seconds=self.__performance_diagnostics_sample_interval_seconds(),
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

    def __breadcrumb_trace_max_entries(self) -> Optional[int]:
        general_config = getattr(self.config, "general", None)
        if general_config is None:
            return None
        max_entries = getattr(general_config, "breadcrumb_trace_max_entries", DEFAULT_BREADCRUMB_TRACE_MAX_ENTRIES)
        if type(max_entries) is not int or max_entries < 0:
            return None
        return None if max_entries == 0 else max_entries

    def __breadcrumb_trace_memory_budget_bytes(self) -> int:
        general_config = getattr(self.config, "general", None)
        if general_config is None:
            return DEFAULT_BREADCRUMB_TRACE_MEMORY_BUDGET_BYTES
        budget = getattr(
            general_config,
            "breadcrumb_trace_memory_budget_bytes",
            DEFAULT_BREADCRUMB_TRACE_MEMORY_BUDGET_BYTES,
        )
        return budget if type(budget) is int and budget > 0 else DEFAULT_BREADCRUMB_TRACE_MEMORY_BUDGET_BYTES

    def __breadcrumb_trace_policy(self) -> Mapping[str, object]:
        general_config = getattr(self.config, "general", None)
        if general_config is None:
            return parse_breadcrumb_trace_policy(DEFAULT_BREADCRUMB_TRACE_POLICY)
        serialized = getattr(general_config, "breadcrumb_trace_policy", DEFAULT_BREADCRUMB_TRACE_POLICY)
        if not isinstance(serialized, str):
            serialized = DEFAULT_BREADCRUMB_TRACE_POLICY
        try:
            return parse_breadcrumb_trace_policy(serialized)
        except ValueError:
            # Config loading validates this field.  Keep Context construction
            # fail-safe for test doubles and legacy callers that bypass Config.
            return parse_breadcrumb_trace_policy(DEFAULT_BREADCRUMB_TRACE_POLICY)

    def __persist_breadcrumb_trace_policy(self, policy: Mapping[str, object]) -> None:
        """Persist policy atomically through Config's existing owner/lock."""
        if not getattr(self.config, "file_path", None):
            raise RuntimeError("breadcrumb policy persistence is not configured")
        with self.config.write_lock:
            previous = self.config.general.breadcrumb_trace_policy
            try:
                self.config.general.breadcrumb_trace_policy = json.dumps(policy, separators=(",", ":"), sort_keys=True)
                self.config.to_file()
            except Exception:
                self.config.general.breadcrumb_trace_policy = previous
                raise

    def __performance_diagnostics_enabled(self) -> bool:
        general_config = getattr(self.config, "general", None)
        enabled = getattr(general_config, "performance_diagnostics_enabled", False)
        return enabled if type(enabled) is bool else False

    def __performance_diagnostics_retention_depth(self) -> int:
        general_config = getattr(self.config, "general", None)
        value = getattr(general_config, "performance_diagnostics_retention_depth", 120)
        return value if type(value) is int and 1 <= value <= 240 else 120

    def __performance_diagnostics_sample_interval_seconds(self) -> int:
        general_config = getattr(self.config, "general", None)
        value = getattr(general_config, "performance_diagnostics_sample_interval_seconds", 5)
        return value if type(value) is int and 1 <= value <= 3600 else 5

    def __redact_config_log_value(self, section: str, option: str, value: Any) -> Any:
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

    def print_to_log(self) -> None:
        # Print the config
        self.logger.debug("Config:")
        config_dict = self.config.as_dict()
        for section in config_dict.keys():
            for option in config_dict[section].keys():
                value = config_dict[section][option]
                value = self.__redact_config_log_value(section, option, value)
                self.logger.debug("  {}.{}: {}".format(section, option, value))

        path_pairs: list[PathPair] = []
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
