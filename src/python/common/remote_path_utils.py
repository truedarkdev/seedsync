# Copyright 2017, Inderpreet Singh, All rights reserved.

import re
import shlex


_SAFE_TILDE_PREFIX = re.compile(r"^~(?:[A-Za-z0-9_.-]+)?(?:/|$)")


def escape_remote_path_for_shell(path: str, allow_tilde_expansion: bool = False) -> str:
    """
    Escape a remote path for shell interpolation.

    When tilde expansion is enabled, keep a safe leading `~` or `~user`
    prefix unquoted and quote the remainder so the shell can expand the home
    directory without interpreting the rest as shell syntax.
    """
    if allow_tilde_expansion and _SAFE_TILDE_PREFIX.match(path):
        if path == "~":
            return "~"

        tilde_prefix_end = path.find("/", 1)
        if tilde_prefix_end == -1:
            return path

        return "{}{}".format(path[:tilde_prefix_end], shlex.quote(path[tilde_prefix_end:]))

    return shlex.quote(path)
