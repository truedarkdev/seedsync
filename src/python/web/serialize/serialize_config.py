# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import collections
from typing import TypeGuard

from common import Config
from ..config_restart import requires_restart


_SENSITIVE_FIELDS = Config.SENSITIVE_FIELDS

_REMOTE_DETAIL_FIELDS = ("remote_address", "remote_username", "remote_path", "remote_python_path")

_REDACTED = Config.REDACTED_SENTINEL


def _is_config_dict(value: object) -> TypeGuard[dict[str, dict[str, object]]]:
    return isinstance(value, dict)


class SerializeConfig:
    @staticmethod
    def config(config: Config) -> str:
        config_value: object = config.as_dict()
        if not _is_config_dict(config_value):
            raise TypeError("Config serialization requires a section mapping")
        config_dict = config_value
        redact_remote_details = getattr(config.general, "config_api_redact_remote_details", True)

        # Make the section names lower case
        keys = list(config_dict.keys())
        config_dict_lowercase: collections.OrderedDict[str, dict[str, object]] = collections.OrderedDict()
        for key in keys:
            config_dict_lowercase[key.lower()] = config_dict[key]

        for section, fields in _SENSITIVE_FIELDS.items():
            if section in config_dict_lowercase:
                section_dict = config_dict_lowercase[section]
                for field in fields:
                    if field in section_dict:
                        section_dict[field] = _REDACTED

        if "notifications" in config_dict_lowercase:
            section_dict = config_dict_lowercase["notifications"]
            section_dict["webhook_url_configured"] = bool(config.notifications.webhook_url)
            section_dict["hmac_secret_configured"] = bool(config.notifications.hmac_secret)
            section_dict["apprise_url_configured"] = bool(config.notifications.apprise_url)

        if redact_remote_details and "lftp" in config_dict_lowercase:
            section_dict = config_dict_lowercase["lftp"]
            for field in _REMOTE_DETAIL_FIELDS:
                if field in section_dict:
                    section_dict[field] = _REDACTED

        restart_required: collections.OrderedDict[str, collections.OrderedDict[str, bool]] = collections.OrderedDict()
        for section, section_dict in config_dict_lowercase.items():
            restart_required[section] = collections.OrderedDict(
                (field, requires_restart(section, field))
                for field in section_dict.keys()
            )

        out_dict: collections.OrderedDict[str, object] = collections.OrderedDict(config_dict_lowercase)
        out_dict["restart_required"] = restart_required
        return json.dumps(out_dict)
