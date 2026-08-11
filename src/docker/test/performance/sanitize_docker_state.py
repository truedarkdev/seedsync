"""Write fixed-field Docker lab summaries without retaining inspect payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _int_or_none(value: str) -> int | None:
    try:
        return int(value) if value not in ("", "<no value>") else None
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _write(path: str, payload: dict[str, object]) -> None:
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _container(path: str, role: str, fields: str) -> None:
    values = fields.split("\t")
    values += [""] * (13 - len(values))
    (
        status,
        running,
        started,
        finished,
        restarts,
        image,
        platform,
        size_rw,
        size_rootfs,
        nano_cpus,
        memory,
        pids_limit,
        mounts,
    ) = values[:13]
    _write(
        path,
        {
            "schema": "seedsync.performance-lab.container-state.v1",
            "role": role,
            "status": status or None,
            "running": _bool_or_none(running),
            "started_at": started or None,
            "finished_at": finished or None,
            "restart_count": _int_or_none(restarts),
            "image": image or None,
            "platform": platform or None,
            "size_rw_bytes": _int_or_none(size_rw),
            "size_rootfs_bytes": _int_or_none(size_rootfs),
            "mount_count": _int_or_none(mounts),
            "resource_limits": {
                "nano_cpus": _int_or_none(nano_cpus),
                "memory_bytes": _int_or_none(memory),
                "pids_limit": _int_or_none(pids_limit),
            },
        },
    )


def _image(path: str, role: str, fields: str) -> None:
    values = fields.split("\t")
    values += [""] * (5 - len(values))
    architecture, operating_system, created, size, layers = values[:5]
    _write(
        path,
        {
            "schema": "seedsync.performance-lab.image-state.v1",
            "role": role,
            "architecture": architecture or None,
            "os": operating_system or None,
            "created_at": created or None,
            "size_bytes": _int_or_none(size),
            "layer_count": _int_or_none(layers),
        },
    )


def _stats(path: str, role: str, fields: str) -> None:
    values = fields.split("\t")
    values += [""] * (3 - len(values))
    cpu, memory, pids = values[:3]
    try:
        cpu_value = float(cpu.rstrip("%")) if cpu else None
    except (AttributeError, ValueError):
        cpu_value = None
    try:
        memory_value = float(memory.rstrip("%")) if memory else None
    except (AttributeError, ValueError):
        memory_value = None
    _write(
        path,
        {
            "schema": "seedsync.performance-lab.container-stats.v1",
            "role": role,
            "cpu_percent": cpu_value,
            "memory_percent": memory_value,
            "process_count": _int_or_none(pids),
        },
    )


def _processes(path: str, role: str, count: str) -> None:
    _write(
        path,
        {
            "schema": "seedsync.performance-lab.process-state.v1",
            "role": role,
            "process_count": _int_or_none(count),
        },
    )


def main() -> int:
    if len(sys.argv) < 5:
        raise SystemExit("usage: sanitize_docker_state.py KIND OUTPUT ROLE VALUE")
    kind, path, role, value = sys.argv[1:5]
    if kind == "container":
        _container(path, role, value)
    elif kind == "image":
        _image(path, role, value)
    elif kind == "stats":
        _stats(path, role, value)
    elif kind == "processes":
        _processes(path, role, value)
    else:
        raise SystemExit(f"unknown summary kind: {kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
