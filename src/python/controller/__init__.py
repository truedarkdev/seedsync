# Copyright 2017, Inderpreet Singh, All rights reserved.

from importlib import import_module


_LAZY_EXPORTS = {
    "Controller": (".controller", "Controller"),
    "ControllerJob": (".controller_job", "ControllerJob"),
    "ControllerPersist": (".controller_persist", "ControllerPersist"),
    "ControllerMemoryMonitor": (".memory_monitor", "ControllerMemoryMonitor"),
    "ModelBuilder": (".model_builder", "ModelBuilder"),
    "AutoQueue": (".auto_queue", "AutoQueue"),
    "AutoQueuePersist": (".auto_queue", "AutoQueuePersist"),
    "IAutoQueuePersistListener": (".auto_queue", "IAutoQueuePersistListener"),
    "AutoQueuePattern": (".auto_queue", "AutoQueuePattern"),
    "IScanner": (".scan.scanner_process", "IScanner"),
    "ScannerResult": (".scan.scanner_process", "ScannerResult"),
    "ScannerProcess": (".scan.scanner_process", "ScannerProcess"),
    "ScannerError": (".scan.scanner_process", "ScannerError"),
    "ValidateProcess": (".validate", "ValidateProcess"),
    "ValidateStatus": (".validate", "ValidateStatus"),
    "ValidateStatusResult": (".validate", "ValidateStatusResult"),
}


def __getattr__(name):
    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "Controller", "ControllerJob", "ControllerPersist", "ControllerMemoryMonitor",
    "ModelBuilder", "AutoQueue", "AutoQueuePersist", "IAutoQueuePersistListener",
    "AutoQueuePattern", "IScanner", "ScannerResult", "ScannerProcess", "ScannerError",
    "ValidateProcess", "ValidateStatus", "ValidateStatusResult",
]
