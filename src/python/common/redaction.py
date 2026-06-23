# Copyright 2026, SeedSync Contributors, All rights reserved.

import re
from typing import Any, Optional


_LFTP_COMMAND_CREDENTIAL_REGEX = re.compile(r"(-u\s+\S+,)\S+")
_PASSWORD_VALUE_REGEX = re.compile(
    r"((?:remote_)?password\s*[=:]\s*)\S+",
    re.IGNORECASE
)
_SECRET_VALUE_REGEX = re.compile(
    r"((?:api[_-]?key|token|secret|credential|authorization|passwd)\s*[=:]\s*)\S+",
    re.IGNORECASE
)
_CREDENTIAL_URL_REGEX = re.compile(
    r"(?P<scheme>\b(?:sftp|ftp|ftps))://"
    r"(?P<userinfo>[^\s]+)"
    r"@"
    r"(?P<host>"
    r"(?:\[[^\]\s]+\])|"
    r"(?:\d{1,3}(?:\.\d{1,3}){3})|"
    r"(?:[a-zA-Z0-9_][a-zA-Z0-9_-]*(?:\.[a-zA-Z0-9_-]+)+)|"
    r"(?:[a-zA-Z0-9_][a-zA-Z0-9_-]*)"
    r")"
    r"(?P<port>:\d+)?",
    re.IGNORECASE
)
_USER_AT_HOST_REGEX = re.compile(
    r"(^|[\s\'\"\[])([a-zA-Z0-9_][\w.\-]*)@("
    r"(?:\d{1,3}(?:\.\d{1,3}){3})|"
    r"(?:[a-zA-Z0-9_][a-zA-Z0-9_-]*)|"
    r"(?:[a-zA-Z0-9][a-zA-Z0-9-]*(?:\.[a-zA-Z0-9-]+)+)"
    r")(?=[\s\'\"\],:/>])",
    re.MULTILINE
)


def _redact_credential_url(match: re.Match) -> str:
    scheme = match.group("scheme")
    port = match.group("port") or ""
    return "{}://**REDACTED**@**REDACTED**{}".format(scheme, port)


def redact_sensitive_text(text: Any) -> Optional[str]:
    if text is None:
        return None
    redacted = text if isinstance(text, str) else str(text)
    redacted = _LFTP_COMMAND_CREDENTIAL_REGEX.sub(r"\1**REDACTED**", redacted)
    redacted = _CREDENTIAL_URL_REGEX.sub(_redact_credential_url, redacted)
    redacted = _PASSWORD_VALUE_REGEX.sub(r"\1**REDACTED**", redacted)
    redacted = _SECRET_VALUE_REGEX.sub(r"\1**REDACTED**", redacted)
    redacted = _USER_AT_HOST_REGEX.sub(r"\1**REDACTED**@**REDACTED**", redacted)
    return redacted
