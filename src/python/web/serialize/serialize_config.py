# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import collections

from common import Config


_SENSITIVE_FIELDS = Config.SENSITIVE_FIELDS

_REMOTE_DETAIL_FIELDS = ("remote_address", "remote_username", "remote_path")

_REDACTED = Config.REDACTED_SENTINEL


class SerializeConfig:
    @staticmethod
    def config(config: Config) -> str:
        config_dict = config.as_dict()
        redact_remote_details = getattr(config.general, "config_api_redact_remote_details", True)

        # Make the section names lower case
        keys = list(config_dict.keys())
        config_dict_lowercase = collections.OrderedDict()
        for key in keys:
            config_dict_lowercase[key.lower()] = config_dict[key]

        for section, fields in _SENSITIVE_FIELDS.items():
            if section in config_dict_lowercase:
                section_dict = config_dict_lowercase[section]
                for field in fields:
                    if field in section_dict:
                        section_dict[field] = _REDACTED

        if redact_remote_details and "lftp" in config_dict_lowercase:
            section_dict = config_dict_lowercase["lftp"]
            for field in _REMOTE_DETAIL_FIELDS:
                if field in section_dict:
                    section_dict[field] = _REDACTED

        return json.dumps(config_dict_lowercase)
