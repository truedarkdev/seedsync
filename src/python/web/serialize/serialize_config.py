# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import collections

from common import Config


_SENSITIVE_FIELDS = {
    "lftp": ["remote_password", "remote_address", "remote_username", "remote_path"],
    "general": ["api_token"],
}

_REDACTED = "**REDACTED**"


class SerializeConfig:
    @staticmethod
    def config(config: Config) -> str:
        config_dict = config.as_dict()

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

        return json.dumps(config_dict_lowercase)
