# Copyright 2017, Inderpreet Singh, All rights reserved.

from importlib import import_module


_LAZY_EXPORTS = {
    "IScanner": (".scanner_process", "IScanner"),
    "ScannerResult": (".scanner_process", "ScannerResult"),
    "ScannerProcess": (".scanner_process", "ScannerProcess"),
    "ScannerError": (".scanner_process", "ScannerError"),
    "ActiveScanner": (".active_scanner", "ActiveScanner"),
    "MultiPathActiveScanner": (".multi_path_active_scanner", "MultiPathActiveScanner"),
    "LocalScanner": (".local_scanner", "LocalScanner"),
    "MultiPathLocalScanner": (".multi_path_scanner", "MultiPathLocalScanner"),
    "MultiPathRemoteScanner": (".multi_path_scanner", "MultiPathRemoteScanner"),
    "RemoteScanLease": (".remote_scanner", "RemoteScanLease"),
    "RemoteScanner": (".remote_scanner", "RemoteScanner"),
}
_LAZY_MODULES = {
    "scanner_process": ".scanner_process",
    "active_scanner": ".active_scanner",
    "multi_path_active_scanner": ".multi_path_active_scanner",
    "local_scanner": ".local_scanner",
    "multi_path_scanner": ".multi_path_scanner",
    "remote_scanner": ".remote_scanner",
}

__all__ = [
    "IScanner", "ScannerResult", "ScannerProcess", "ScannerError",
    "ActiveScanner", "MultiPathActiveScanner", "LocalScanner",
    "MultiPathLocalScanner", "MultiPathRemoteScanner", "RemoteScanLease", "RemoteScanner",
    "scanner_process", "active_scanner", "multi_path_active_scanner", "local_scanner",
    "multi_path_scanner", "remote_scanner",
]


def __getattr__(name):
    if name in _LAZY_MODULES:
        return import_module(_LAZY_MODULES[name], __name__)

    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | set(_LAZY_MODULES))
