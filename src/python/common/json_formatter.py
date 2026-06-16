import json
import logging
import traceback


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    _TRACEBACK_MARKER = "Traceback (most recent call last):"

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        traceback_text = self._get_traceback_text(record, message)
        if traceback_text and message.endswith(traceback_text):
            message = message[:len(message) - len(traceback_text)].rstrip("\n")

        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "process": record.processName,
            "thread": record.threadName,
        }
        if traceback_text:
            log_entry["traceback"] = traceback_text

        return json.dumps(log_entry)

    @classmethod
    def _get_traceback_text(cls, record: logging.LogRecord, message: str) -> str:
        if record.exc_info and record.exc_info[2]:
            return "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            return record.exc_text

        marker_index = message.find(cls._TRACEBACK_MARKER)
        if marker_index == -1:
            return None

        line_start = message.rfind("\n", 0, marker_index) + 1
        return message[line_start:]
