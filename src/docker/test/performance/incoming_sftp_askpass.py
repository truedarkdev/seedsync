#!/usr/bin/env python3
"""Private SSH_ASKPASS bridge for the Incoming root-only SFTP probe."""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import sys


def _password_from_protected_config(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        if os.name != "nt":
            mode = path.stat().st_mode & 0o777
            if mode & 0o007 or not mode & 0o400:
                return None
        content = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(content)
            if not isinstance(payload, dict):
                return None
            connection = payload.get("sftp", payload.get("lftp", payload))
            password = connection.get("password") if isinstance(connection, dict) else None
        except json.JSONDecodeError:
            parser = configparser.RawConfigParser(interpolation=None)
            parser.read_string(content)
            password = parser["Lftp"].get("remote_password") if parser.has_section("Lftp") else None
        return password if isinstance(password, str) and password else None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, configparser.Error):
        return None


def main() -> int:
    if os.environ.get("INCOMING_RECOVERY_SFTP_ASKPASS") != "1":
        return 1
    reference = os.environ.get("INCOMING_RECOVERY_SFTP_ASKPASS_CONFIG")
    if not reference:
        return 1
    password = _password_from_protected_config(Path(reference))
    if password is None:
        return 1
    sys.stdout.write(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
