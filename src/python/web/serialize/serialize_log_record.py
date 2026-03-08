# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import logging
import re

from .serialize import Serialize


class SerializeLogRecord(Serialize):
    """
    This class defines the serialization interface between python backend
    and the EventSource client frontend for the log stream.
    """
    # Event keys
    __EVENT_RECORD = "log-record"

    # Data keys
    __KEY_TIME = "time"
    __KEY_LEVEL_NAME = "level_name"
    __KEY_LOGGER_NAME = "logger_name"
    __KEY_MESSAGE = "message"
    __KEY_EXCEPTION_TRACEBACK = "exc_tb"

    def __init__(self):
        super().__init__()
        # logging formatter to generate exception traceback
        self.__log_formatter = logging.Formatter()

    @staticmethod
    def _redact_sensitive(message):
        if message is None:
            return None

        message = re.sub(r'(-u\s+\S+,)\S+', r'\1**REDACTED**', message)
        message = re.sub(
            r'((?:remote_)?password\s*[=:]\s*)\S+',
            r'\1**REDACTED**',
            message,
            flags=re.IGNORECASE
        )
        return message

    def record(self, record: logging.LogRecord) -> str:
        json_dict = dict()
        json_dict[SerializeLogRecord.__KEY_TIME] = str(record.created)
        json_dict[SerializeLogRecord.__KEY_LEVEL_NAME] = record.levelname
        json_dict[SerializeLogRecord.__KEY_LOGGER_NAME] = record.name
        message = record.getMessage() if record.msg is not None else None
        json_dict[SerializeLogRecord.__KEY_MESSAGE] = SerializeLogRecord._redact_sensitive(
            message
        )
        exc_text = None
        if record.exc_text:
            exc_text = SerializeLogRecord._redact_sensitive(record.exc_text)
        elif record.exc_info:
            exc_text = SerializeLogRecord._redact_sensitive(
                self.__log_formatter.formatException(record.exc_info)
            )
        json_dict[SerializeLogRecord.__KEY_EXCEPTION_TRACEBACK] = exc_text

        record_json = json.dumps(json_dict)
        return self._sse_pack(event=SerializeLogRecord.__EVENT_RECORD, data=record_json)
