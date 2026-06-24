# Copyright 2017, Inderpreet Singh, All rights reserved.

import configparser
import re
from typing import Dict
from io import StringIO
import collections
from abc import ABC
from typing import Type, TypeVar, Callable, Any

from .error import AppError
from .persist import Persist, PersistError
from .types import overrides


def _strtobool(value: str) -> int:
    """Local replacement for distutils.util.strtobool removed in Python 3.12."""
    lower = value.lower()
    if lower in ('y', 'yes', 't', 'true', 'on', '1'):
        return 1
    if lower in ('n', 'no', 'f', 'false', 'off', '0'):
        return 0
    raise ValueError("Invalid truth value: {!r}".format(value))


class ConfigError(AppError):
    """
    Exception indicating a bad config value
    """
    pass


InnerConfigType = Dict[str, Any]
OuterConfigType = Dict[str, InnerConfigType]


# Source: https://stackoverflow.com/a/39205612/8571324
T = TypeVar('T', bound='InnerConfig')


_BYTE_SIZE_VALUE_RE = re.compile(r"^(?P<size>\d+)(?P<suffix>[KMG])?$", re.IGNORECASE)
_LOG_LEVEL_VALUES = frozenset(("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
_LOG_FORMAT_VALUES = frozenset(("standard", "json"))
_TRANSFER_PROTOCOL_VALUES = frozenset(("sftp", "ftps"))


def _normalize_log_level(config_cls: Any, name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("Bad config: {}.{} ({}) must be a log level value".format(
            config_cls.__name__, name, value
        ))
    normalized = value.strip().upper()
    if not normalized:
        raise ConfigError("Bad config: {}.{} is empty".format(
            config_cls.__name__, name
        ))
    if normalized not in _LOG_LEVEL_VALUES:
        raise ConfigError("Bad config: {}.{} ({}) must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL".format(
            config_cls.__name__, name, value
        ))
    return normalized


def _normalize_log_format(config_cls: Any, name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("Bad config: {}.{} ({}) must be either standard or json".format(
            config_cls.__name__, name, value
        ))
    normalized = value.strip().lower()
    if not normalized:
        raise ConfigError("Bad config: {}.{} is empty".format(
            config_cls.__name__, name
        ))
    if normalized not in _LOG_FORMAT_VALUES:
        raise ConfigError("Bad config: {}.{} ({}) must be either standard or json".format(
            config_cls.__name__, name, value
        ))
    return normalized


def _normalize_transfer_protocol(config_cls: Any, name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("Bad config: {}.{} ({}) must be either sftp or ftps".format(
            config_cls.__name__, name, value
        ))
    normalized = value.strip().lower()
    if not normalized:
        raise ConfigError("Bad config: {}.{} is empty".format(
            config_cls.__name__, name
        ))
    if normalized not in _TRANSFER_PROTOCOL_VALUES:
        raise ConfigError("Bad config: {}.{} ({}) must be either sftp or ftps".format(
            config_cls.__name__, name, value
        ))
    return normalized


def _normalize_remote_python_path(config_cls: Any, name: str, value: Any) -> str:
    if value is None:
        return "python3"
    if not isinstance(value, str):
        raise ConfigError("Bad config: {}.{} ({}) must be a string value".format(
            config_cls.__name__, name, value
        ))
    normalized = value.strip()
    if not normalized:
        return "python3"
    return normalized


class Converters:
    @staticmethod
    def null(_: Any, __: str, value: str) -> str:
        return value

    @staticmethod
    def int(config_cls: Any, name: str, value: str) -> int:
        if not value:
            raise ConfigError("Bad config: {}.{} is empty".format(
                config_cls.__name__, name
            ))
        try:
            val = int(value)
        except ValueError:
            raise ConfigError("Bad config: {}.{} ({}) must be an integer value".format(
                config_cls.__name__, name, value
            ))
        return val

    @staticmethod
    def bool(config_cls: Any, name: str, value: str) -> bool:
        if not value:
            raise ConfigError("Bad config: {}.{} is empty".format(
                config_cls.__name__, name
            ))
        try:
            val = bool(_strtobool(value))
        except ValueError:
            raise ConfigError("Bad config: {}.{} ({}) must be a boolean value".format(
                config_cls.__name__, name, value
            ))
        return val

    @staticmethod
    def log_level(config_cls: Any, name: str, value: str) -> str:
        return _normalize_log_level(config_cls, name, value)

    @staticmethod
    def transfer_protocol(config_cls: Any, name: str, value: str) -> str:
        return _normalize_transfer_protocol(config_cls, name, value)

    @staticmethod
    def remote_python_path(config_cls: Any, name: str, value: str) -> str:
        return _normalize_remote_python_path(config_cls, name, value)


class Checkers:
    @staticmethod
    def null(_: Any, __: str, value: Any) -> Any:
        return value

    @staticmethod
    def bool_value(config_cls: Any, name: str, value: bool) -> bool:
        if type(value) is not bool:
            raise ConfigError("Bad config: {}.{} ({}) must be a boolean value".format(
                config_cls.__name__, name, value
            ))
        return value

    @staticmethod
    def string_nonempty(config_cls: Any, name: str, value: str) -> str:
        if not value or not value.strip():
            raise ConfigError("Bad config: {}.{} is empty".format(
                config_cls.__name__, name
            ))
        return value

    @staticmethod
    def string_allow_empty(config_cls: Any, name: str, value: str) -> str:
        if value != "" and (not value or not value.strip()):
            raise ConfigError("Bad config: {}.{} is empty".format(
                config_cls.__name__, name
            ))
        return value

    @staticmethod
    def log_level(config_cls: Any, name: str, value: str) -> str:
        return _normalize_log_level(config_cls, name, value)

    @staticmethod
    def log_format(config_cls: Any, name: str, value: str) -> str:
        return _normalize_log_format(config_cls, name, value)

    @staticmethod
    def transfer_protocol(config_cls: Any, name: str, value: str) -> str:
        return _normalize_transfer_protocol(config_cls, name, value)

    @staticmethod
    def remote_python_path(config_cls: Any, name: str, value: str) -> str:
        return _normalize_remote_python_path(config_cls, name, value)

    @staticmethod
    def int_non_negative(config_cls: Any, name: str, value: int) -> int:
        if value < 0:
            raise ConfigError("Bad config: {}.{} ({}) must be zero or greater".format(
                config_cls.__name__, name, value
            ))
        return value

    @staticmethod
    def int_non_negative_max(max_val: int) -> Callable:
        def _checker(config_cls: Any, name: str, value: int) -> int:
            if value < 1:
                raise ConfigError("Bad config: {}.{} ({}) must be greater than 0".format(
                    config_cls.__name__, name, value
                ))
            if value > max_val:
                raise ConfigError("Bad config: {}.{} ({}) must not exceed {} (FD_SETSIZE limit)".format(
                    config_cls.__name__, name, value, max_val
                ))
            return value
        return _checker

    @staticmethod
    def int_positive(config_cls: Any, name: str, value: int) -> int:
        if value < 1:
            raise ConfigError("Bad config: {}.{} ({}) must be greater than 0".format(
                config_cls.__name__, name, value
            ))
        return value

    @staticmethod
    def byte_size_or_empty(config_cls: Any, name: str, value: Any) -> str:
        if type(value) is int:
            value = str(value)
        if not isinstance(value, str):
            raise ConfigError("Bad config: {}.{} ({}) must be a byte size value".format(
                config_cls.__name__, name, value
            ))
        if value == "":
            return value
        normalized = value.strip()
        if not normalized:
            raise ConfigError("Bad config: {}.{} is empty".format(
                config_cls.__name__, name
            ))
        if not _BYTE_SIZE_VALUE_RE.match(normalized):
            raise ConfigError("Bad config: {}.{} ({}) must be a byte size value like 512K, 8M, 1G, or 8388608".format(
                config_cls.__name__, name, value
            ))
        suffix = normalized[-1]
        if suffix.isalpha():
            normalized = normalized[:-1] + suffix.upper()
        return normalized


class InnerConfig(ABC):
    """
    Abstract base class for a config section
    Config values are exposed as properties. They must be set using their native type.
    Internal utility methods are provided to convert strings to native types. These are
    only used when creating config from a dict.

    Implementation details:
    Each property has associated with is a checker and a converter function.
    The checker function performs boundary check on the native type value.
    The converter function converts the string representation into the native type.
    """
    class PropMetadata:
        """Tracks property metadata"""
        def __init__(self, checker: Callable, converter: Callable):
            self.checker = checker
            self.converter = converter

    # Global map to map a property to its metadata
    # Is there a way for each concrete class to do this separately?
    __prop_addon_map = collections.OrderedDict()

    @classmethod
    def _create_property(cls, name: str, checker: Callable, converter: Callable) -> property:
        # noinspection PyProtectedMember
        prop = property(fget=lambda s: s._get_property(name),
                        fset=lambda s, v: s._set_property(name, v, checker))
        prop_addon = InnerConfig.PropMetadata(checker=checker, converter=converter)
        InnerConfig.__prop_addon_map[prop] = prop_addon
        return prop

    def _get_property(self, name: str) -> Any:
        return getattr(self, "__" + name, None)

    def _set_property(self, name: str, value: Any, checker: Callable):
        # Allow setting to None for the first time
        if value is None and self._get_property(name) is None:
            setattr(self, "__" + name, None)
        else:
            setattr(self, "__" + name, checker(self.__class__, name, value))

    @classmethod
    def from_dict(cls: Type[T], config_dict: InnerConfigType) -> T:
        """
        Construct and return inner config from a dict
        Dict values can be either native types, or str representations
        :param config_dict:
        :return:
        """
        config_dict = dict(config_dict)  # copy that we can modify

        # Loop over all the property name, and set them to the value given in config_dict
        # Raise error if a matching key is not found in config_dict
        # noinspection PyCallingNonCallable
        inner_config = cls()
        property_map = {p: getattr(cls, p) for p in dir(cls) if isinstance(getattr(cls, p), property)}
        for name, prop in property_map.items():
            if name not in config_dict:
                raise ConfigError("Missing config: {}.{}".format(cls.__name__, name))
            inner_config.set_property(name, config_dict[name])
            del config_dict[name]

        # Raise error if a key in config_dict did not match a property
        extra_keys = config_dict.keys()
        if extra_keys:
            raise ConfigError("Unknown config: {}.{}".format(cls.__name__, next(iter(extra_keys))))

        return inner_config

    def as_dict(self) -> InnerConfigType:
        """
        Return the dict representation of the inner config
        :return:
        """
        config_dict = collections.OrderedDict()
        cls = self.__class__
        my_property_to_name_map = {getattr(cls, p): p for p in dir(cls) if isinstance(getattr(cls, p), property)}
        # Arrange prop names in order of creation. Use the prop map to get the order
        # Prop map contains all properties of all config classes, so filtering is required
        all_properties = InnerConfig.__prop_addon_map.keys()
        for prop in all_properties:
            if prop in my_property_to_name_map.keys():
                name = my_property_to_name_map[prop]
                config_dict[name] = getattr(self, name)
        return config_dict

    def has_property(self, name: str) -> bool:
        """
        Returns true if the given property exists, false otherwise
        :param name:
        :return:
        """
        try:
            return isinstance(getattr(self.__class__, name), property)
        except AttributeError:
            return False

    def set_property(self, name: str, value: Any):
        """
        Set a property dynamically
        Do a str conversion of the value, if necessary
        :param name:
        :param value:
        :return:
        """
        cls = self.__class__
        prop_addon = InnerConfig.__prop_addon_map[getattr(cls, name)]
        # Do the conversion if value is of type str
        native_value = prop_addon.converter(cls, name, value) if type(value) is str else value
        # Set the property, which will invoke the checker
        # noinspection PyProtectedMember
        self._set_property(name, native_value, prop_addon.checker)


# Useful aliases
IC = InnerConfig
# noinspection PyProtectedMember
PROP = InnerConfig._create_property


class Config(Persist):
    """
    Configuration registry
    """
    REDACTED_SENTINEL = "**REDACTED**"
    LEGACY_REDACTED_SENTINEL = "********"
    REDACTED_SENTINELS = frozenset((REDACTED_SENTINEL, LEGACY_REDACTED_SENTINEL))
    SENSITIVE_FIELDS: Dict[str, tuple[str, ...]] = collections.OrderedDict([
        ("general", ("api_token", "webhook_secret")),
        ("lftp", ("remote_password",)),
    ])

    @classmethod
    def is_sensitive_field(cls, section: str, key: str) -> bool:
        if not isinstance(section, str) or not isinstance(key, str):
            return False
        return key in cls.SENSITIVE_FIELDS.get(section.lower(), ())

    @classmethod
    def is_redacted_value(cls, value: Any) -> bool:
        return isinstance(value, str) and value in cls.REDACTED_SENTINELS

    class General(IC):
        log_level = PROP("log_level", Checkers.log_level, Converters.log_level)
        verbose = PROP("verbose", Checkers.null, Converters.bool)
        api_token = PROP("api_token", Checkers.null, Converters.null)
        allowed_hostname = PROP("allowed_hostname", Checkers.null, Converters.null)
        trusted_browser_bootstrap_remote_addrs = PROP("trusted_browser_bootstrap_remote_addrs",
                                                      Checkers.null,
                                                      Converters.null)
        browser_handover_recovery_version = PROP("browser_handover_recovery_version",
                                                 Checkers.null,
                                                 Converters.null)
        breadcrumb_trace_enabled = PROP("breadcrumb_trace_enabled",
                                        Checkers.bool_value,
                                        Converters.bool)
        breadcrumb_trace_retention_depth = PROP("breadcrumb_trace_retention_depth",
                                                Checkers.int_non_negative_max(1024),
                                                Converters.int)
        config_api_redact_remote_details = PROP("config_api_redact_remote_details",
                                                Checkers.bool_value,
                                                Converters.bool)

        def __init__(self):
            super().__init__()
            self.log_level = "INFO"
            self.verbose = None
            self.api_token = None
            self.allowed_hostname = None
            self.trusted_browser_bootstrap_remote_addrs = None
            self.browser_handover_recovery_version = None
            self.breadcrumb_trace_enabled = False
            self.breadcrumb_trace_retention_depth = 128
            self.config_api_redact_remote_details = True

        @classmethod
        def from_dict(cls: Type[T], config_dict: InnerConfigType) -> T:
            if "api_token" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["api_token"] = ""
            if "allowed_hostname" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["allowed_hostname"] = ""
            if "trusted_browser_bootstrap_remote_addrs" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["trusted_browser_bootstrap_remote_addrs"] = ""
            if "browser_handover_recovery_version" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["browser_handover_recovery_version"] = ""
            if "log_level" not in config_dict:
                config_dict = dict(config_dict)
                if "debug" in config_dict:
                    debug_value = config_dict.pop("debug")
                    if type(debug_value) is str:
                        debug_value = Converters.bool(cls, "debug", debug_value)
                    else:
                        debug_value = Checkers.bool_value(cls, "debug", debug_value)
                    config_dict["log_level"] = "DEBUG" if debug_value else "INFO"
                else:
                    config_dict["log_level"] = "INFO"
            else:
                config_dict = dict(config_dict)
                config_dict.pop("debug", None)
            if "breadcrumb_trace_enabled" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["breadcrumb_trace_enabled"] = False
            if "breadcrumb_trace_retention_depth" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["breadcrumb_trace_retention_depth"] = 128
            if "config_api_redact_remote_details" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["config_api_redact_remote_details"] = True
            return super().from_dict(config_dict)

        def has_property(self, name: str) -> bool:
            if name == "debug":
                return True
            return super().has_property(name)

        def set_property(self, name: str, value: Any):
            if name == "debug":
                debug_value = Converters.bool(self.__class__, name, value) if type(value) is str else value
                debug_value = Checkers.bool_value(self.__class__, name, debug_value)
                super().set_property("log_level", "DEBUG" if debug_value else "INFO")
                return
            super().set_property(name, value)

    class Lftp(IC):
        remote_address = PROP("remote_address", Checkers.string_nonempty, Converters.null)
        remote_username = PROP("remote_username", Checkers.string_nonempty, Converters.null)
        remote_password = PROP("remote_password", Checkers.string_allow_empty, Converters.null)
        remote_port = PROP("remote_port", Checkers.int_positive, Converters.int)
        remote_path = PROP("remote_path", Checkers.string_nonempty, Converters.null)
        local_path = PROP("local_path", Checkers.string_nonempty, Converters.null)
        remote_path_to_scan_script = PROP("remote_path_to_scan_script", Checkers.string_nonempty, Converters.null)
        remote_python_path = PROP("remote_python_path", Checkers.remote_python_path, Converters.remote_python_path)
        use_ssh_key = PROP("use_ssh_key", Checkers.null, Converters.bool)
        num_max_parallel_downloads = PROP("num_max_parallel_downloads", Checkers.int_positive, Converters.int)
        num_max_parallel_files_per_download = PROP("num_max_parallel_files_per_download",
                                                   Checkers.int_positive,
                                                   Converters.int)
        num_max_connections_per_root_file = PROP("num_max_connections_per_root_file",
                                                 Checkers.int_positive,
                                                 Converters.int)
        num_max_connections_per_dir_file = PROP("num_max_connections_per_dir_file",
                                                Checkers.int_positive,
                                                Converters.int)
        num_max_total_connections = PROP("num_max_total_connections",
                                         Checkers.int_non_negative_max(32),
                                         Converters.int)
        use_temp_file = PROP("use_temp_file", Checkers.null, Converters.bool)
        rate_limit = PROP("rate_limit", Checkers.null, Converters.null)
        net_socket_buffer = PROP("net_socket_buffer", Checkers.byte_size_or_empty, Converters.null)
        staging_path = PROP("staging_path", Checkers.null, Converters.null)
        protocol = PROP("protocol", Checkers.transfer_protocol, Converters.transfer_protocol)
        remote_ftp_port = PROP("remote_ftp_port", Checkers.int_positive, Converters.int)
        ftp_ssl_verify_certificate = PROP("ftp_ssl_verify_certificate", Checkers.bool_value, Converters.bool)

        def __init__(self):
            super().__init__()
            self.remote_address = None
            self.remote_username = None
            self.remote_password = None
            self.remote_port = None
            self.remote_path = None
            self.local_path = None
            self.remote_path_to_scan_script = None
            self.remote_python_path = "python3"
            self.use_ssh_key = None
            self.num_max_parallel_downloads = None
            self.num_max_parallel_files_per_download = None
            self.num_max_connections_per_root_file = None
            self.num_max_connections_per_dir_file = None
            self.num_max_total_connections = None
            self.use_temp_file = None
            self.rate_limit = None
            self.net_socket_buffer = None
            self.staging_path = None
            self.protocol = "sftp"
            self.remote_ftp_port = 21
            self.ftp_ssl_verify_certificate = True

        @classmethod
        def from_dict(cls: Type[T], config_dict: InnerConfigType) -> T:
            config_dict = dict(config_dict)
            if "remote_python_path" not in config_dict:
                config_dict["remote_python_path"] = "python3"
            if "net_socket_buffer" not in config_dict:
                config_dict["net_socket_buffer"] = "8M"
            if "staging_path" not in config_dict:
                config_dict["staging_path"] = ""
            if "protocol" not in config_dict:
                config_dict["protocol"] = "sftp"
            if "remote_ftp_port" not in config_dict:
                config_dict["remote_ftp_port"] = 21
            if "ftp_ssl_verify_certificate" not in config_dict:
                config_dict["ftp_ssl_verify_certificate"] = True
            return super().from_dict(config_dict)

    class Controller(IC):
        interval_ms_remote_scan = PROP("interval_ms_remote_scan", Checkers.int_positive, Converters.int)
        interval_ms_local_scan = PROP("interval_ms_local_scan", Checkers.int_positive, Converters.int)
        interval_ms_downloading_scan = PROP("interval_ms_downloading_scan", Checkers.int_positive, Converters.int)
        extract_path = PROP("extract_path", Checkers.string_nonempty, Converters.null)
        use_local_path_as_extract_path = PROP("use_local_path_as_extract_path", Checkers.null, Converters.bool)
        managed_extract_folders_enabled = PROP("managed_extract_folders_enabled", Checkers.null, Converters.bool)

        def __init__(self):
            super().__init__()
            self.interval_ms_remote_scan = None
            self.interval_ms_local_scan = None
            self.interval_ms_downloading_scan = None
            self.extract_path = None
            self.use_local_path_as_extract_path = None
            self.managed_extract_folders_enabled = True

        @classmethod
        def from_dict(cls: Type[T], config_dict: InnerConfigType) -> T:
            if "managed_extract_folders_enabled" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["managed_extract_folders_enabled"] = True
            return super().from_dict(config_dict)

    class Web(InnerConfig):
        port = PROP("port", Checkers.int_positive, Converters.int)

        def __init__(self):
            super().__init__()
            self.port = None

    class AutoQueue(InnerConfig):
        enabled = PROP("enabled", Checkers.null, Converters.bool)
        patterns_only = PROP("patterns_only", Checkers.null, Converters.bool)
        auto_extract = PROP("auto_extract", Checkers.null, Converters.bool)
        auto_delete_remote = PROP("auto_delete_remote", Checkers.bool_value, Converters.bool)

        def __init__(self):
            super().__init__()
            self.enabled = None
            self.patterns_only = None
            self.auto_extract = None
            self.auto_delete_remote = False

        @classmethod
        def from_dict(cls: Type[T], config_dict: InnerConfigType) -> T:
            if "auto_delete_remote" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["auto_delete_remote"] = False
            return super().from_dict(config_dict)

    class Logging(IC):
        log_format = PROP("log_format", Checkers.log_format, Converters.null)

        def __init__(self):
            super().__init__()
            self.log_format = "standard"

        @classmethod
        def from_dict(cls: Type[T], config_dict: InnerConfigType) -> T:
            if "log_format" not in config_dict:
                config_dict = dict(config_dict)
                config_dict["log_format"] = "standard"
            elif isinstance(config_dict["log_format"], str) and not config_dict["log_format"].strip():
                config_dict = dict(config_dict)
                config_dict["log_format"] = "standard"
            return super().from_dict(config_dict)

    def __init__(self):
        self.file_path: str | None = None
        self.general = Config.General()
        self.lftp = Config.Lftp()
        self.controller = Config.Controller()
        self.web = Config.Web()
        self.autoqueue = Config.AutoQueue()
        self.logging = Config.Logging()

    @staticmethod
    def _check_section(dct: OuterConfigType, name: str) -> InnerConfigType:
        if name not in dct:
            raise ConfigError("Missing config section: {}".format(name))
        val = dct[name]
        del dct[name]
        return val

    @staticmethod
    def _check_empty_outer_dict(dct: OuterConfigType):
        extra_keys = dct.keys()
        if extra_keys:
            raise ConfigError("Unknown section: {}".format(next(iter(extra_keys))))

    @classmethod
    @overrides(Persist)
    def from_str(cls: type["Config"], content: str) -> "Config":
        # Values are opaque user data and must survive '%' round-trips verbatim.
        config_parser = configparser.ConfigParser(interpolation=None)
        try:
            config_parser.read_string(content)
        except (
                configparser.MissingSectionHeaderError,
                configparser.ParsingError
        ) as e:
            raise PersistError("Error parsing Config - {}: {}".format(
                type(e).__name__, str(e))
            )
        config_dict = {}
        for section in config_parser.sections():
            config_dict[section] = {}
            for option in config_parser.options(section):
                config_dict[section][option] = config_parser.get(section, option)
        return cls.from_dict(config_dict)

    @classmethod
    def from_file(cls, file_path: str) -> "Config":
        config = super().from_file(file_path)
        config.file_path = file_path
        return config

    @overrides(Persist)
    def to_str(self) -> str:
        # Keep write/read behavior aligned with from_str for percent-bearing values.
        config_parser = configparser.ConfigParser(interpolation=None)
        config_dict = self.as_dict()
        for section in config_dict:
            config_parser.add_section(section)
            section_dict = config_dict[section]
            for key in section_dict:
                config_parser.set(section, key, str(section_dict[key]))
        str_io = StringIO()
        config_parser.write(str_io)
        return str_io.getvalue()

    def to_file(self, file_path: str | None = None):
        if file_path is None:
            file_path = self.file_path
        if file_path is None:
            raise PersistError("Config file path is not bound")
        self.file_path = file_path
        super().to_file(file_path)

    @staticmethod
    def from_dict(config_dict: OuterConfigType) -> "Config":
        config_dict = dict(config_dict)  # copy that we can modify
        config = Config()

        config.general = Config.General.from_dict(Config._check_section(config_dict, "General"))
        config.lftp = Config.Lftp.from_dict(Config._check_section(config_dict, "Lftp"))
        config.controller = Config.Controller.from_dict(Config._check_section(config_dict, "Controller"))
        config.web = Config.Web.from_dict(Config._check_section(config_dict, "Web"))
        config.autoqueue = Config.AutoQueue.from_dict(Config._check_section(config_dict, "AutoQueue"))
        config.logging = Config.Logging.from_dict(config_dict.pop("Logging", {}))

        Config._check_empty_outer_dict(config_dict)
        return config

    def as_dict(self) -> OuterConfigType:
        # We convert all values back to strings
        # Use an ordered dict to main section order
        config_dict = collections.OrderedDict()
        config_dict["General"] = self.general.as_dict()
        config_dict["Lftp"] = self.lftp.as_dict()
        config_dict["Controller"] = self.controller.as_dict()
        config_dict["Web"] = self.web.as_dict()
        config_dict["AutoQueue"] = self.autoqueue.as_dict()
        config_dict["Logging"] = self.logging.as_dict()
        return config_dict

    def has_section(self, name: str) -> bool:
        """
        Returns true if the given section exists, false otherwise
        :param name:
        :return:
        """
        try:
            return isinstance(getattr(self, name), InnerConfig)
        except AttributeError:
            return False
