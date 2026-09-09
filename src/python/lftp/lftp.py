# Copyright 2017, Inderpreet Singh, All rights reserved.

import hashlib
import io
import json
import logging
import re
import os
import select
import stat
import sys
import time
import secrets
from functools import wraps
from typing import Any, Callable, Union, List, Optional, Dict, Iterable, Concatenate, ParamSpec, Protocol, TypeVar

# 3rd party libs
import pexpect

# my libs
from common import AppError
from common.breadcrumb_trace import opaque_trace_correlation
from common.config import Checkers
from common.exclude_patterns import ExactPathExclusion, partition_transfer_exclusions
from common.lftp_status import MAX_LFTP_PGET_STATUS_BYTES, parse_lftp_pget_status_bytes
from common.redaction import redact_sensitive_text
from .job_status_parser import LftpJobStatus, LftpJobStatusParser, LftpJobStatusParserError


# How many status errors are allowed before error propagates out
MAX_CONSECUTIVE_STATUS_ERRORS = 10
MAX_KILL_MATCH_ATTEMPTS = 20
STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS = 1.0
STATUS_POLL_TERMINAL_DRAIN_MAX_BYTES = 256 * 1024
STATUS_POLL_TERMINAL_DRAIN_READ_BYTES = 64 * 1024
LFTP_STATUS_POLL_FAILURE_REASONS = frozenset({
    "timeout", "eof", "command_error", "parser_error", "terminal_backlog", "unhealthy_snapshot",
})
LFTP_SIDECAR_TRACE_CATEGORY = "lftp.sidecar"
LFTP_SIDECAR_TRACE_SCHEMA = "lftp.sidecar.v1"
LFTP_SIDECAR_TRACE_CLASSIFICATIONS = frozenset({
    "missing", "valid", "unsafe", "orphan", "ambiguous", "stale", "malformed",
})
LFTP_PATH_PAIR_TRACE_CATEGORY = "transfer.lftp"
LFTP_PATH_PAIR_TRACE_SCHEMA = "lftp.path_pair_annotation.v1"
LFTP_COMMAND_TRACE_CATEGORY = "transfer.lftp.command"
LFTP_COMMAND_TRACE_SCHEMA = "lftp.command_boundary.v1"
LFTP_COMMAND_TRACE_KINDS = frozenset({"get", "mirror", "pget"})
LFTP_COMMAND_TRACE_PHASES = frozenset({"submitted", "prompt_ready", "prompt_timeout", "process_eof", "backend_error"})
LFTP_PTY_TRACE_CATEGORY = "transfer.lftp.pty"
LFTP_PTY_TRACE_SCHEMA = "lftp.pty_boundary.v1"
LFTP_PTY_TRACE_PHASES = frozenset({"pre_write", "write", "prompt"})
LFTP_STATUS_POLL_TRACE_CATEGORY = "transfer.lftp.status"
# This opt-in is intentionally process-start configuration.  Its records are
# emitted to the container's stderr so a parser stall cannot prevent a host
# observer from recovering the pre-parse boundary through Docker logs.
_PREPARSE_STDERR_REMAINING = 256
_PRIVATE_STATUS_FRAME_CAPTURE_REMAINING = 3
_PRIVATE_STATUS_FRAME_CAPTURE_MAX_BYTES = 64 * 1024
LFTP_STATUS_POLL_TRACE_SCHEMA = "lftp.status_poll.v1"
LFTP_STATUS_POLL_TRACE_PHASES = frozenset({
    "submitted", "jobs_read", "prompt_ready", "prompt_timeout", "process_eof", "command_error",
    "backend_error", "terminal_backlog", "parse_started", "parse_complete", "parse_error", "health",
})
LFTP_STATUS_POLL_TRACE_FAILURE_PHASES = frozenset({
    "prompt_timeout", "process_eof", "command_error", "backend_error", "parse_error",
    "terminal_backlog",
})
redact_credentials = redact_sensitive_text
_P = ParamSpec("_P")
_R = TypeVar("_R")
_INCOMING_RECOVERY_DIAGNOSTIC_ENV = "INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS"
_INCOMING_RECOVERY_PHASES = frozenset({
    "precheck", "drain", "send", "prompt", "connecting", "error_recovery", "teardown", "unknown",
})
_INCOMING_RECOVERY_STATUS_RESULT_SHAPES = frozenset({
    "empty_or_prompt", "queue_done", "job_present", "ambiguous", "parse_error", "unknown",
})
_INCOMING_RECOVERY_STATUS_RECOVERIES = frozenset({"none", "connection_grace", "unknown"})


def _incoming_recovery_buffer_length(value: object) -> int | None:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value)
    if isinstance(value, io.BytesIO):
        try:
            view = value.getbuffer()
            try:
                return view.nbytes
            finally:
                view.release()
        except Exception:
            return None
    return None


def _incoming_recovery_duration_bucket(started_at: object, ended_at: object) -> str:
    try:
        elapsed_ms = max(0, int((float(ended_at) - float(started_at)) * 1000))
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    if elapsed_ms <= 4:
        return "0-4"
    if elapsed_ms <= 19:
        return "5-19"
    if elapsed_ms <= 99:
        return "20-99"
    if elapsed_ms <= 499:
        return "100-499"
    if elapsed_ms <= 1999:
        return "500-1999"
    return "2000+"


def _incoming_recovery_buffer_observation(process: object) -> tuple[str, str]:
    """Measure one unread PTY buffer source without combining matched frames."""
    try:
        attributes = vars(process)
    except Exception:
        return "unavailable", "unknown"
    # pexpect's public ``buffer`` compatibility property calls ``getvalue()``.
    # Read only raw instance attributes so this exact-600 diagnostic cannot
    # materialize a large retained PTY frame.
    public_length = _incoming_recovery_buffer_length(attributes.get("buffer"))
    if public_length is not None:
        return "public_buffer", _lftp_trace_bytes_bucket(public_length)
    private_length = _incoming_recovery_buffer_length(attributes.get("_buffer"))
    if private_length is not None:
        return "private_buffer", _lftp_trace_bytes_bucket(private_length)
    return "unavailable", "unknown"


def _incoming_recovery_status_result_shape(output: object, statuses: object) -> str:
    """Classify a parsed status result without retaining its private frame."""
    if statuses is None:
        return "parse_error"
    if isinstance(statuses, list) and statuses:
        return "job_present"
    if not isinstance(output, str):
        return "ambiguous"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        # ``before`` excludes a matched prompt, so this cannot distinguish a
        # genuinely idle queue from a prompt-only response.
        return "empty_or_prompt"
    if any(re.match(r"^\[\d+\]\s+(?:mirror|pget|get|put)\b", line) or
           re.search(r"\b\d+/\d+\s+\(\d+%\)", line) for line in lines):
        return "job_present"
    if any(re.match(r"^\[\d+\]\s+Done\s+\(queue\s+\(", line) for line in lines):
        return "queue_done"
    return "ambiguous"


def _incoming_recovery_diagnostic_enabled() -> bool:
    return os.environ.get(_INCOMING_RECOVERY_DIAGNOSTIC_ENV) == "600"


def _incoming_recovery_child_record(
        recorder: object, process: object, error: object = None, classification: str = "unknown",
        *, phase: str = "unknown", started_at: object = None,
        alive_before: object = None, command_kind: object = "unknown",
) -> None:
    if not _incoming_recovery_diagnostic_enabled() or not callable(recorder):
        return
    try:
        alive_after = process.isalive() if process is not None else None
    except Exception:
        alive_after = None
    try:
        exitstatus = getattr(process, "exitstatus", None)
    except Exception:
        exitstatus = None
    try:
        signalstatus = getattr(process, "signalstatus", None)
    except Exception:
        signalstatus = None
    if isinstance(error, pexpect.exceptions.EOF):
        classification = "eof"
    elif isinstance(error, pexpect.exceptions.TIMEOUT):
        classification = "timeout"
    elif isinstance(error, (OSError, pexpect.exceptions.ExceptionPexpect, AppError)):
        classification = "command_error"
    if isinstance(signalstatus, int):
        classification = "signal"
    elif isinstance(exitstatus, int) and classification == "unknown":
        classification = "exit"
    safe_phase = phase if phase in _INCOMING_RECOVERY_PHASES else "unknown"
    buffer_source, read_buffer_byte_length_bucket = _incoming_recovery_buffer_observation(process)
    reaped = (
        "signal" if isinstance(signalstatus, int) else
        "exit" if isinstance(exitstatus, int) else
        "alive" if alive_after is True else
        "not_alive" if alive_after is False else "unknown"
    )
    process_pid = "unavailable"
    try:
        pid = getattr(process, "pid", None)
        if type(pid) is int and pid > 0:
            process_pid = pid
    except Exception:
        pass
    ended_at = time.monotonic()
    try:
        recorder("lftp_child", {
            "phase": safe_phase,
            "duration_bucket": _incoming_recovery_duration_bucket(started_at, ended_at),
            "process_pid": process_pid,
            "process_alive_before": alive_before if type(alive_before) is bool else None,
            "process_alive_after": alive_after if type(alive_after) is bool else None,
            "process_alive": alive_after if type(alive_after) is bool else None,
            "reaped": reaped,
            "exitstatus": exitstatus, "signalstatus": signalstatus,
            "classification": classification,
            "command_kind": command_kind if command_kind in {"queue", "status", "unknown"} else "unknown",
            "read_buffer_source": buffer_source,
            "read_buffer_byte_length_bucket": read_buffer_byte_length_bucket,
        })
    except Exception:
        pass


def _breadcrumb_effectively_enabled(trace: object, category: str, level: str) -> bool:
    if trace is None:
        return False
    enabled = getattr(trace, "is_enabled", None)
    if callable(enabled):
        try:
            if not bool(enabled()):
                return False
        except Exception:
            return False
    gate = getattr(trace, "is_effectively_enabled", None)
    if callable(gate):
        try:
            return bool(gate(category, level))
        except Exception:
            return False
    return True


def _record_lftp_sidecar_breadcrumb(
        breadcrumb_trace: object,
        classification: str,
        target_identity: object,
        *,
        target_presence: str = "unknown",
        sidecar_presence: str = "unknown",
        sidecar_size: str = "unknown",
        flow_id: object = None,
) -> None:
    """Record one bounded queue-sidecar decision without runtime identities."""
    if classification not in LFTP_SIDECAR_TRACE_CLASSIFICATIONS:
        return
    level = "warning" if classification in {
        "unsafe", "orphan", "ambiguous", "stale", "malformed",
    } else "info"
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, LFTP_SIDECAR_TRACE_CATEGORY, level):
        return
    try:
        recorder = getattr(breadcrumb_trace, "record", None)
        if not callable(recorder):
            return
        coverage = {
            "target": target_presence if target_presence in ("known", "unknown") else "unknown",
            "sidecar": sidecar_presence if sidecar_presence in ("known", "unknown") else "unknown",
            "sidecar_size": sidecar_size if sidecar_size in ("known", "unknown") else "unknown",
        }
        coalesce_key = opaque_trace_correlation(
            "lftp.sidecar|{}|{}|{}|{}|{}".format(
                target_identity,
                classification,
                coverage["target"],
                coverage["sidecar"],
                coverage["sidecar_size"],
            ),
        )
        recorder(
            "lftp",
            "lftp_sidecar_classified",
            {
                "schema": LFTP_SIDECAR_TRACE_SCHEMA,
                "classification": classification,
                "coverage": coverage,
            },
            stage="lftp_sidecar_validation",
            event_type="diagnostic",
            category=LFTP_SIDECAR_TRACE_CATEGORY,
            level=level,
            corr_id=opaque_trace_correlation(
                "lftp.sidecar|{}".format(target_identity),
            ),
            flow_id=(
                _safe_lftp_queue_trace_flow(flow_id)
                if os.environ.get("INCOMING_RECOVERY_EXPERIMENTAL_AUTHORITY_TIMEOUT_SECS") == "600"
                else None
            ),
            _coalesce_key=coalesce_key,
            trace_scope="flow",
        )
    except Exception:
        # Breadcrumb diagnostics must never alter Queue admission.
        return


def _record_lftp_path_pair_annotation(
        breadcrumb_trace: object,
        status: LftpJobStatus,
        remote_match_count: int,
        local_match_count: int,
        result: str,
        reason: str,
) -> None:
    """Record a bounded path-pair selection outcome without runtime paths."""
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, LFTP_PATH_PAIR_TRACE_CATEGORY, "info"):
        return
    try:
        recorder = getattr(breadcrumb_trace, "record", None)
        if not callable(recorder):
            return
        remote_match_count = min(max(int(remote_match_count), 0), 2)
        local_match_count = min(max(int(local_match_count), 0), 2)
        if result not in {"selected", "unscoped"}:
            result = "unscoped"
        if reason not in {
                "matched", "remote_only", "remote_no_match", "remote_multiple_matches",
                "local_conflict",
        }:
            reason = "unknown"
        job_correlation = getattr(status, "job_correlation", None)
        if not isinstance(job_correlation, str):
            job_correlation = None
        details = {
            "schema": LFTP_PATH_PAIR_TRACE_SCHEMA,
            "remote_match_count": remote_match_count,
            "remote_match_cardinality": "zero" if remote_match_count == 0 else
            "one" if remote_match_count == 1 else "multiple",
            "local_match_count": local_match_count,
            "local_match_cardinality": "zero" if local_match_count == 0 else
            "one" if local_match_count == 1 else "multiple",
            "result": result,
            "reason": reason,
        }
        recorder(
            "lftp",
            "lftp_path_pair_annotation",
            details,
            stage="lftp_path_pair_annotation",
            event_type="diagnostic",
            category=LFTP_PATH_PAIR_TRACE_CATEGORY,
            level="info",
            corr_id=job_correlation,
            _coalesce_key=opaque_trace_correlation(
                "lftp.path_pair_annotation|{}|{}|{}|{}|{}".format(
                    job_correlation or "none",
                    remote_match_count,
                    local_match_count,
                    result,
                    reason,
                ),
            ),
            trace_scope="flow",
        )
    except Exception:
        # Breadcrumb diagnostics must never alter status polling or annotation.
        return


def _safe_lftp_queue_trace_flow(value: object) -> Optional[str]:
    """Accept only the controller's opaque Queue-flow shape."""
    prefix = "fractional-queue:"
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    suffix = value[len(prefix):]
    if len(suffix) != 16 or any(character not in "0123456789abcdef" for character in suffix):
        return None
    return value


def _safe_lftp_pty_correlation(value: object) -> Optional[str]:
    prefix = "lftp-pty:"
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    suffix = value[len(prefix):]
    return value if len(suffix) == 16 and all(c in "0123456789abcdef" for c in suffix) else None


def _safe_lftp_status_poll_correlation(value: object) -> Optional[str]:
    """Accept the fixed opaque poll token shared with model lineage."""
    prefix = "lftp-poll:"
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    suffix = value[len(prefix):]
    if len(suffix) != 16 or any(character not in "0123456789abcdef" for character in suffix):
        return None
    return value


def _lftp_trace_count_bucket(value: object) -> str:
    if not isinstance(value, int) or value < 0:
        return "unknown"
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 4:
        return "2-4"
    if value <= 16:
        return "5-16"
    if value <= 64:
        return "17-64"
    if value <= 256:
        return "65-256"
    return "257+"


def _lftp_trace_bytes_bucket(value: object) -> str:
    if not isinstance(value, int) or value < 0:
        return "unknown"
    if value == 0:
        return "0"
    if value <= 127:
        return "1-127"
    if value <= 511:
        return "128-511"
    if value <= 2047:
        return "512-2047"
    if value <= 8191:
        return "2048-8191"
    if value <= 32767:
        return "8192-32767"
    return "32768+"


def _record_lftp_preparse_stderr(correlation: object, output: object, process_alive: object,
                                 trace: object) -> None:
    """Emit one bounded, host-retrievable parser-entry record when explicitly enabled.

    This deliberately shares the existing opt-in status category and opaque poll
    correlation.  It never serializes status content, commands, paths, or names.
    """
    global _PREPARSE_STDERR_REMAINING
    if os.environ.get("SEEDSYNC_LFTP_PREPARSE_STDERR") != "1" or \
            _PREPARSE_STDERR_REMAINING <= 0:
        return
    safe_correlation = _safe_lftp_status_poll_correlation(correlation)
    if safe_correlation is None or not _breadcrumb_effectively_enabled(
            trace, LFTP_STATUS_POLL_TRACE_CATEGORY, "debug"):
        return
    output_text = "" if output is None else str(output)
    try:
        byte_count = len(output_text.encode("utf-8", "surrogateescape"))
    except UnicodeEncodeError:
        byte_count = -1
    record = (
        "seedsync_lftp_preparse corr={} phase=parse_started bytes={} lines={} process_alive={}\n".format(
            safe_correlation,
            _lftp_trace_bytes_bucket(byte_count),
            _lftp_trace_count_bucket(len(output_text.splitlines())),
            1 if process_alive is True else 0,
        )
    ).encode("ascii")
    _PREPARSE_STDERR_REMAINING -= 1
    try:
        stderr_fd = sys.stderr.fileno()
        # Do not let a congested Docker log pipe become another status-poll
        # stall.  One small write is attempted only when immediately writable;
        # a missing record is an explicit diagnostic limitation, never a retry.
        if stderr_fd not in select.select([], [stderr_fd], [], 0)[1]:
            return
        os.write(stderr_fd, record)
    except (AttributeError, OSError, ValueError):
        # Independent diagnostics must never affect the poll or transfer.
        return


def _lftp_private_status_frame_capture(
        correlation: object, output: object, process_alive: object, trace: object,
        boundary: dict[str, object],
) -> None:
    """Atomically publish a few parser inputs in an explicitly mounted private directory.

    This is an opt-in, local diagnosis path.  It intentionally emits no logger or
    breadcrumb record: status output can contain paths and credentials, while the
    paired JSON manifest holds only bounded structural framing facts.
    """
    global _PRIVATE_STATUS_FRAME_CAPTURE_REMAINING
    capture_dir = os.environ.get("SEEDSYNC_LFTP_PRIVATE_STATUS_FRAME_CAPTURE_DIR")
    if not capture_dir or _PRIVATE_STATUS_FRAME_CAPTURE_REMAINING <= 0:
        return
    safe_correlation = _safe_lftp_status_poll_correlation(correlation)
    if safe_correlation is None or not _breadcrumb_effectively_enabled(
            trace, LFTP_STATUS_POLL_TRACE_CATEGORY, "debug"):
        return
    try:
        if not os.path.isdir(capture_dir):
            return
        output_bytes = ("" if output is None else str(output)).encode("utf-8", "surrogateescape")
        token = "{}-{}".format(
            hashlib.sha256(safe_correlation.encode("ascii")).hexdigest()[:16],
            secrets.token_hex(4),
        )
        capture_path = os.path.join(capture_dir, "lftp-status-{}".format(token))
        temporary_path = os.path.join(capture_dir, ".lftp-status-{}.tmp".format(token))
        captured = output_bytes[:_PRIVATE_STATUS_FRAME_CAPTURE_MAX_BYTES]
        manifest = {
            "schema": "seedsync.lftp.private-status-frame.v1",
            "correlation": safe_correlation,
            "process_alive": process_alive is True,
            "original_byte_count": len(output_bytes),
            "captured_byte_count": len(captured),
            "truncated": len(captured) != len(output_bytes),
            "sha256": hashlib.sha256(output_bytes).hexdigest(),
            "boundary": boundary,
        }
        os.mkdir(temporary_path, 0o700)
        raw_fd = os.open(os.path.join(temporary_path, "frame.bin"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            offset = 0
            while offset < len(captured):
                written = os.write(raw_fd, captured[offset:])
                if written <= 0:
                    raise OSError("private frame capture write made no progress")
                offset += written
        finally:
            os.close(raw_fd)
        manifest_fd = os.open(os.path.join(temporary_path, "manifest.json"),
                              os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
            offset = 0
            while offset < len(manifest_bytes):
                written = os.write(manifest_fd, manifest_bytes[offset:])
                if written <= 0:
                    raise OSError("private frame manifest write made no progress")
                offset += written
        finally:
            os.close(manifest_fd)
        os.replace(temporary_path, capture_path)
        _PRIVATE_STATUS_FRAME_CAPTURE_REMAINING -= 1
    except (OSError, TypeError, UnicodeError, ValueError):
        # Capture must never change polling, parsing, or transfer behavior.
        return


def _lftp_boundary_byte_count(value: object) -> int:
    """Return a structural byte count without decoding status content."""
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", "surrogateescape"))
    return -1


def _lftp_pty_trace_enabled(trace: object, level: str) -> bool:
    """PTY boundaries require their own explicit opt-in rule."""
    explicit = getattr(trace, "is_explicitly_configured", None)
    if not callable(explicit):
        return False
    try:
        return explicit(LFTP_PTY_TRACE_CATEGORY) is True and \
            _breadcrumb_effectively_enabled(trace, LFTP_PTY_TRACE_CATEGORY, level)
    except Exception:
        return False


def _lftp_diagnostic_exception_family(error: object) -> str:
    """Map known errors to the fixed privacy-safe diagnostic enum."""
    if isinstance(error, pexpect.exceptions.TIMEOUT):
        return "timeout"
    if isinstance(error, pexpect.exceptions.EOF):
        return "eof"
    if isinstance(error, OSError):
        return "os_error"
    if isinstance(error, LftpJobStatusParserError):
        return "parser"
    if isinstance(error, RuntimeError) or isinstance(error, LftpError):
        return "runtime"
    return "unknown"


def _record_lftp_pty_breadcrumb(trace: object, flow_id: object, phase: str, *,
                                readiness: str, send_mode: str, prompt_outcome: str,
                                process_alive: object, error: object = None) -> None:
    """Record a bounded PTY boundary; command bytes and content never leave this function."""
    safe_flow = _safe_lftp_pty_correlation(flow_id)
    if safe_flow is None or phase not in LFTP_PTY_TRACE_PHASES:
        return
    failure = prompt_outcome in {"send_error", "prompt_timeout", "process_eof", "runtime_error"}
    level = "warning" if failure else "debug"
    if not _lftp_pty_trace_enabled(trace, level):
        return
    try:
        recorder = getattr(trace, "record", None)
        if not callable(recorder):
            return
        details = {
            "schema": LFTP_PTY_TRACE_SCHEMA, "phase": phase,
            "pre_write_readiness": readiness if readiness in {"ready", "not_required", "unknown"} else "unknown",
            "send_mode": send_mode if send_mode in {"send", "sendline"} else "unknown",
            "prompt_outcome": prompt_outcome,
            "process_alive": process_alive is True,
        }
        if error is not None:
            details["exception_family"] = _lftp_diagnostic_exception_family(error)
        recorder("lftp", "lftp_pty_boundary", details, stage="lftp_pty_boundary",
           event_type="failure" if failure else "state_transition",
           category=LFTP_PTY_TRACE_CATEGORY, level=level, corr_id=safe_flow, flow_id=safe_flow,
           _coalesce_key=opaque_trace_correlation("lftp.pty|{}|{}|{}".format(safe_flow, phase, prompt_outcome)),
           trace_scope="flow")
    except Exception:
        return


def _record_lftp_command_breadcrumb(
        breadcrumb_trace: object,
        command_kind: object,
        phase: object,
        flow_id: object,
        *,
        process_alive: object,
        output: object = None,
        command_metrics: Optional[tuple[int, int, int]] = None,
) -> None:
    """Record the privacy-safe Queue/PTy boundary, never its command or output."""
    if command_kind not in LFTP_COMMAND_TRACE_KINDS or phase not in LFTP_COMMAND_TRACE_PHASES:
        return
    level = "warning" if phase in {"prompt_timeout", "process_eof", "backend_error"} else "debug"
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, LFTP_COMMAND_TRACE_CATEGORY, level):
        return
    try:
        recorder = getattr(breadcrumb_trace, "record", None)
        if not callable(recorder):
            return
        safe_flow_id = _safe_lftp_queue_trace_flow(flow_id)
        output_text = "" if output is None else str(output)
        if not output_text:
            output_class = "empty"
        elif "Connecting..." in output_text:
            output_class = "connecting"
        elif Lftp.__detect_errors_from_output(output_text):
            output_class = "backend_error"
        else:
            output_class = "other"
        corr_seed = safe_flow_id if safe_flow_id is not None else "{}:{}".format(command_kind, phase)
        argument_count, exclude_count, command_byte_length = command_metrics or (-1, -1, -1)
        recorder(
            "lftp",
            "lftp_command_boundary",
            {
                "schema": LFTP_COMMAND_TRACE_SCHEMA,
                # ``command_kind`` would be intentionally redacted by the
                # generic privacy sanitizer; this fixed transfer enum is safe.
                "transfer_kind": command_kind,
                "phase": phase,
                "process_alive": process_alive is True,
                "output_class": output_class,
                "argument_count_bucket": _lftp_trace_count_bucket(argument_count),
                "exclude_count_bucket": _lftp_trace_count_bucket(exclude_count),
                "exact_exclusion_count": exclude_count,
                # Do not use a ``command``-named key: the generic sanitizer
                # correctly treats that as command content rather than this safe bucket.
                "submission_byte_length_bucket": _lftp_trace_bytes_bucket(command_byte_length),
            },
            stage="lftp_command_boundary",
            event_type="failure" if level == "warning" else "state_transition",
            category=LFTP_COMMAND_TRACE_CATEGORY,
            level=level,
            corr_id="lftp-command:{}".format(opaque_trace_correlation(corr_seed)),
            flow_id=safe_flow_id,
            _coalesce_key=opaque_trace_correlation(
                "lftp.command|{}|{}|{}|{}".format(
                    safe_flow_id or "none", command_kind, phase, output_class,
                ),
            ),
            trace_scope="flow",
        )
    except Exception:
        # Diagnostics must not affect PTY ownership or transfer admission.
        return


def _record_lftp_status_poll_breadcrumb(
        breadcrumb_trace: object, correlation: object, phase: object, *,
        process_alive: object, output: object = None, failure_reason: object = None,
        status_count: object = None, healthy: object = None,
        exception: object = None, boundary: object = None,
        boundary_state: object = None,
) -> None:
    """Record bounded PTY status phases without command, output, or path data."""
    safe_correlation = _safe_lftp_status_poll_correlation(correlation)
    if safe_correlation is None or phase not in LFTP_STATUS_POLL_TRACE_PHASES:
        return
    level = "warning" if phase in LFTP_STATUS_POLL_TRACE_FAILURE_PHASES or \
        (phase == "health" and healthy is False) else "debug"
    if not _breadcrumb_effectively_enabled(breadcrumb_trace, LFTP_STATUS_POLL_TRACE_CATEGORY, level):
        return
    try:
        if not isinstance(failure_reason, str) or failure_reason not in LFTP_STATUS_POLL_FAILURE_REASONS:
            failure_reason = "none"
        output_text = "" if output is None else str(output)
        if not output_text:
            output_class = "empty"
        elif "Connecting..." in output_text:
            output_class = "connecting"
        elif Lftp.__detect_errors_from_output(output_text):
            output_class = "backend_error"
        else:
            output_class = "other"
        boundary = boundary if isinstance(boundary, str) and boundary in {
            "send", "read", "prompt", "parse", "health",
        } else {
            "submitted": "send",
            "jobs_read": "read",
            "prompt_ready": "prompt",
            "prompt_timeout": "prompt",
            "process_eof": "prompt",
            "command_error": "prompt",
            "backend_error": "read",
            "terminal_backlog": "read",
            "parse_started": "parse",
            "parse_complete": "parse",
            "parse_error": "parse",
            "health": "health",
        }.get(phase, "unknown")
        details = {
            "schema": LFTP_STATUS_POLL_TRACE_SCHEMA,
            "phase": phase,
            "boundary": boundary,
            "stage": boundary,
            "outcome": phase,
            "process_alive": process_alive is True,
            "output_class": output_class,
            "read_buffer_byte_length_bucket": _lftp_trace_bytes_bucket(
                len(output_text.encode("utf-8", "surrogateescape")),
            ),
            "failure_reason": failure_reason,
            "status_count_bucket": _lftp_trace_count_bucket(status_count),
        }
        if isinstance(boundary_state, dict):
            details.update({key: value for key, value in boundary_state.items()
                            if key in {"prior_prompt", "send_admitted", "prompt_reached", "retained_before"}})
        if isinstance(healthy, bool):
            details["healthy"] = healthy
            if phase == "health":
                details["outcome"] = "healthy" if healthy else "unhealthy"
        if exception is not None:
            details["exception_family"] = _lftp_diagnostic_exception_family(exception)
        failure = phase in LFTP_STATUS_POLL_TRACE_FAILURE_PHASES or \
            (phase == "health" and healthy is False)
        recorder = getattr(breadcrumb_trace, "record", None)
        if not callable(recorder):
            return
        recorder(
            "lftp", "lftp_status_poll",
            details,
            stage="lftp_status_poll",
            event_type="failure" if failure else "state_transition",
            category=LFTP_STATUS_POLL_TRACE_CATEGORY,
            level=level,
            corr_id=safe_correlation,
            flow_id=safe_correlation,
            _coalesce_key=opaque_trace_correlation(
                "lftp.status|{}|{}|{}|{}".format(
                    safe_correlation, phase, output_class, failure_reason,
                ),
            ),
            trace_scope="flow",
        )
    except Exception:
        # Diagnostics must not affect PTY ownership, status freshness, or transfer admission.
        return


class _PathPair(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def remote_path(self) -> str: ...
    @property
    def local_path(self) -> str: ...


class LftpError(AppError):
    """
    Custom exception that describes the failure of the lftp command
    """
    def __init__(self, message: Any = ""):
        super().__init__(redact_sensitive_text("" if message is None else message))


class Lftp:
    """
    Lftp command utility
    """
    __SET_NUM_PARALLEL_FILES = "mirror:parallel-transfer-count"
    __SET_NUM_CONNECTIONS_PGET = "pget:default-n"
    __SET_NUM_CONNECTIONS_MIRROR = "mirror:use-pget-n"
    __SET_NUM_MAX_TOTAL_CONNECTIONS = "net:connection-limit"
    __SET_RATE_LIMIT = "net:limit-rate"
    __SET_NET_SOCKET_BUFFER = "net:socket-buffer"
    __SET_MIN_CHUNK_SIZE = "pget:min-chunk-size"
    __SET_PGET_SAVE_STATUS = "pget:save-status"
    __SET_NUM_PARALLEL_JOBS = "cmd:queue-parallel"
    __SET_MOVE_BACKGROUND_ON_EXIT = "cmd:move-background"
    __SET_COMMAND_AT_EXIT = "cmd:at-exit"
    __SET_USE_TEMP_FILE = "xfer:use-temp-file"
    __SET_TEMP_FILE_NAME = "xfer:temp-file-name"
    __SET_XFER_VERIFY = "xfer:verify"
    __SET_XFER_VERIFY_COMMAND = "xfer:verify-command"
    __SET_SFTP_AUTO_CONFIRM = "sftp:auto-confirm"
    __SET_SFTP_CONNECT_PROGRAM = "sftp:connect-program"
    __SET_SFTP_SET_PERMISSIONS = "sftp:set-permissions"
    __SET_FTP_SSL_FORCE = "ftp:ssl-force"
    __SET_FTP_SSL_PROTECT_DATA = "ftp:ssl-protect-data"
    __SET_SSL_VERIFY_CERTIFICATE = "ssl:verify-certificate"
    __SET_FTP_SSL_AUTH = "ftp:ssl-auth"
    __SET_FTP_PASSIVE_MODE = "ftp:passive-mode"
    __SET_CMD_SAVE_HISTORY = "cmd:save-rl-history"
    __SET_CMD_SAVE_CWD = "cmd:save-cwd-history"
    __INITIAL_PROMPT_PATTERN = r"lftp.*>[ \t]*"
    __PASSWORD_RESPONSE_PATTERN = r"(?im)^(?:password:|\S+@\S+'s password:)[ \t]*\Z"
    __PGET_STATUS_FILE_SUFFIX = ".lftp-pget-status"
    __LFTP_TEMP_FILE_SUFFIX = ".lftp"

    @staticmethod
    def __has_valid_umask() -> bool:
        umask_value = os.environ.get("UMASK", "")
        if not umask_value:
            return False

        return all(character in "01234567" for character in umask_value)

    @staticmethod
    def __decode_spawn_output(output: Any) -> str:
        if output is None or output is pexpect.EOF or output is pexpect.TIMEOUT:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf8", "replace")
        return str(output)

    @staticmethod
    def __quote_command_argument(value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise LftpError("LFTP command arguments cannot contain control characters")
        escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
        return "\"{}\"".format(escaped)

    @staticmethod
    def __validate_password(password: Optional[str]) -> None:
        if password is not None and any(ord(character) < 32 or ord(character) == 127 for character in password):
            raise LftpError("LFTP password cannot contain control characters")

    @staticmethod
    def __endpoint_prompt_pattern(user: str, address: str) -> str:
        return r"lftp {}@{}:.*>[ \t]*".format(re.escape(user), re.escape(address))

    def __password_prompt_pattern(self, open_command: str) -> str:
        return (
            r"(?i)\A(?:[ \t]*{}\r\n(?:(?:\x1b\[\?2004[hl])+\r)?)?"
            r"(?:password:|\S+@\S+'s password:)[ \t]*\Z"
        ).format(re.escape(open_command))

    def __init__(self,
                 address: str,
                 port: int,
                 user: str,
                 password: Optional[str],
                 protocol: str = "sftp",
                 remote_ftp_port: Optional[int] = None,
                 ssl_verify_certificate: bool = False,
                 use_legacy_lftp_password_argv: bool = False):
        Lftp.__validate_password(password)
        self.__user = user
        self.__password = password
        self.__address = address
        self.__protocol = protocol.strip().lower()
        if self.__protocol not in ("sftp", "ftps"):
            raise ValueError("Invalid lftp protocol '{}': must be 'sftp' or 'ftps'".format(protocol))
        self.__scheme = "ftp" if self.__protocol == "ftps" else "sftp"
        self.__transfer_port = remote_ftp_port if self.__protocol == "ftps" and remote_ftp_port is not None else port
        self.__ssl_verify_certificate = ssl_verify_certificate
        self.__use_legacy_lftp_password_argv = use_legacy_lftp_password_argv
        self.__base_remote_dir_path = ""
        self.__base_local_dir_path = ""
        self.logger = logging.getLogger("Lftp")
        self.__expect_pattern = Lftp.__endpoint_prompt_pattern(self.__user, self.__address)
        self.__job_status_parser = LftpJobStatusParser()
        self.__timeout = 30  # in seconds
        self.__consecutive_status_errors = 0
        self.__path_pairs_by_id: Dict[str, Dict[str, str]] = {}
        self.__last_command_timed_out = False
        self.__last_status_poll_healthy = True
        # Per-poll diagnostic classification only; it is not transfer
        # authority, a cache, or a persisted lifecycle marker.
        self.__last_status_poll_failure_reason: Optional[str] = None
        self.__last_incoming_recovery_status_observation: dict[str, object] | None = None
        self.__status_poll_needs_connection_grace = False
        self.__breadcrumb_trace: object = None

        self.__log_command_output = False
        self.__pending_error = None

        spawn_env = os.environ.copy()
        spawn_env["COLUMNS"] = "10000"
        # Do not allow an ambient lftp variable to become a second credential channel.
        spawn_env.pop("LFTP_PASSWORD", None)
        args: list[str] = []
        password_in_argv = bool(self.__password) and self.__use_legacy_lftp_password_argv
        if password_in_argv:
            self.logger.warning(
                "Using legacy lftp password argv mode; the remote password is visible to same-host "
                "process inspection. Disable Lftp.use_legacy_lftp_password_argv after compatibility testing."
            )
            args = [
                "-p", str(self.__transfer_port),
                "-u", "{},{}".format(self.__user, self.__password if self.__password else ""),
                "{}://{}".format(self.__scheme, self.__address)
            ]
        if not self.__password:
            # Key/no-password auth must let lftp connect with its normal non-secret
            # user argv; `open --user` otherwise prompts for a password on lftp 4.9.
            args = [
                "-p", str(self.__transfer_port),
                "-u", "{},".format(self.__user),
                "{}://{}".format(self.__scheme, self.__address),
            ]
        # Echo must be disabled while pexpect creates the PTY.  Changing it after
        # lftp starts is too late: readline/background output can restore or use
        # the original terminal mode and splice control commands into status.
        self.__process = pexpect.spawn(
            "/usr/bin/lftp", args, env=spawn_env, dimensions=(24, 10000), echo=False
        )  # type: ignore[arg-type]
        try:
            if password_in_argv or not self.__password:
                try:
                    self.__process.expect(self.__expect_pattern)
                except pexpect.exceptions.TIMEOUT:
                    out = self.__decode_spawn_output(self.__process.before).strip()
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "startup"):
                        raise
                self.__setup()
                self.__password = None
            else:
                self.__process.expect(Lftp.__INITIAL_PROMPT_PATTERN)
                self.__expect_pattern = Lftp.__INITIAL_PROMPT_PATTERN
                self.__setup()
                self.__connect_with_native_password_prompt()
        except Exception:
            self.__cleanup_failed_initialization()
            raise

    def __cleanup_failed_initialization(self) -> None:
        self.__password = None
        try:
            if self.__process.isalive():
                self.__process.close(force=True)
        except (OSError, pexpect.exceptions.ExceptionPexpect):
            self.logger.warning("Unable to close lftp process after initialization failure")

    def __connect_with_native_password_prompt(self):
        """Open after protocol safeguards are set, answering a native password prompt without echo."""
        open_command = "open -p {} --user {} {}".format(
            self.__transfer_port,
            Lftp.__quote_command_argument(self.__user),
            Lftp.__quote_command_argument("{}://{}".format(self.__scheme, self.__address)),
        )
        self.__process.sendline(open_command)
        self.__expect_pattern = Lftp.__endpoint_prompt_pattern(self.__user, self.__address)
        try:
            match = self.__process.expect(
                [self.__password_prompt_pattern(open_command), self.__expect_pattern], timeout=self.__timeout
            )
        except pexpect.exceptions.TIMEOUT:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "opening connection"):
                raise LftpError("Lftp process did not connect: {}".format(out))
            raise AssertionError("unreachable")
        except pexpect.exceptions.EOF:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            raise LftpError("Lftp process terminated while opening connection: {}".format(out))

        if match != 0:
            self.__password = None
            return
        if not self.__password:
            raise LftpError("Lftp requested a password but none is configured")

        password = self.__password
        try:
            # Spawn-time echo suppression is retained for the password response.
            # Do not toggle it here: the PTY's false baseline protects the secret
            # and also keeps later control commands out of status output.
            self.__process.sendline(password)
            match = self.__process.expect(
                [Lftp.__PASSWORD_RESPONSE_PATTERN, self.__expect_pattern], timeout=self.__timeout
            )
            if match == 0:
                raise LftpError("Lftp rejected the password")
        except pexpect.exceptions.TIMEOUT:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "submitting password"):
                raise LftpError("Lftp process did not accept password: {}".format(out))
            raise AssertionError("unreachable")
        except pexpect.exceptions.EOF:
            out = self.__redact_password(self.__decode_spawn_output(self.__process.before).strip())
            raise LftpError("Lftp process terminated while submitting password: {}".format(out))
        finally:
            self.__password = None

    def __redact_password(self, value: str) -> str:
        redacted = redact_credentials(value)
        if self.__password:
            return redacted.replace(self.__password, "**REDACTED**")
        return redacted

    def set_verbose_logging(self, verbose: bool):
        self.__log_command_output = verbose

    def __setup(self):
        """
        Setup the lftp instance with default settings
        :return:
        """
        # Set to kill on exit to prevent a zombie process
        self.__set(Lftp.__SET_COMMAND_AT_EXIT, "\"kill all\"")
        # A prompt response is not a command, but disable lftp persistence before opening
        # the connection so no interactive state is written while credentials are in use.
        self.__set(Lftp.__SET_CMD_SAVE_HISTORY, "false")
        self.__set(Lftp.__SET_CMD_SAVE_CWD, "false")
        if self.__protocol == "ftps":
            self.__setup_ftps()
        else:
            self.__setup_sftp()
        # Keep pget status snapshots fresher to reduce valid stop/resume rollback.
        self.__set(Lftp.__SET_PGET_SAVE_STATUS, "2")

    def __setup_sftp(self):
        # Auto-add server to known host file
        self.sftp_auto_confirm = True
        # Let a valid explicit UMASK control downloaded file permissions.
        if Lftp.__has_valid_umask():
            self.__set(Lftp.__SET_SFTP_SET_PERMISSIONS, "false")

    def __setup_ftps(self):
        self.__set(Lftp.__SET_FTP_SSL_FORCE, "true")
        self.__set(Lftp.__SET_FTP_SSL_PROTECT_DATA, "true")
        self.__set(
            Lftp.__SET_SSL_VERIFY_CERTIFICATE,
            "true" if self.__ssl_verify_certificate else "false"
        )
        if not self.__ssl_verify_certificate:
            self.logger.warning(
                "FTPS TLS certificate verification is disabled; the connection is encrypted "
                "but not authenticated against man-in-the-middle attacks."
            )
        self.__set(Lftp.__SET_FTP_SSL_AUTH, "TLS")
        self.__set(Lftp.__SET_FTP_PASSIVE_MODE, "true")
        self.__verify_ftps_ssl_forced()

    def __verify_ftps_ssl_forced(self):
        try:
            ssl_force = Lftp.__to_bool(self.__get(Lftp.__SET_FTP_SSL_FORCE))
        except LftpError as e:
            raise LftpError("FTPS fail-closed check could not read ftp:ssl-force: {}".format(e)) from e
        if not ssl_force:
            raise LftpError(
                "FTPS fail-closed check failed: ftp:ssl-force is not enabled; "
                "refusing to transfer over cleartext FTP"
            )

    @staticmethod
    def with_check_process(
        method: Callable[Concatenate["Lftp", _P], _R]
    ) -> Callable[Concatenate["Lftp", _P], _R]:
        """
        Decorator that checks for a valid process before executing
        the decorated method
        :param method:
        :return:
        """
        @wraps(method)
        def wrapper(inst: "Lftp", *args: _P.args, **kwargs: _P.kwargs) -> _R:
            alive_before = inst.__process.isalive()
            if not alive_before:
                _incoming_recovery_child_record(
                    kwargs.get("diagnostic_recorder"), inst.__process,
                    phase="precheck", alive_before=alive_before,
                    command_kind="status" if kwargs.get("status_poll") is True else "queue",
                )
                raise LftpError("lftp process is not running")
            return method(inst, *args, **kwargs)
        return wrapper

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("Lftp")
        self.__job_status_parser.set_base_logger(self.logger)

    def set_breadcrumb_trace(self, breadcrumb_trace: object) -> None:
        """Set the shared emitter used for bounded Queue sidecar diagnostics."""
        self.__breadcrumb_trace = breadcrumb_trace

    def set_base_remote_dir_path(self, base_remote_dir_path: str):
        self.__base_remote_dir_path = base_remote_dir_path

    def set_base_local_dir_path(self, base_local_dir_path: str):
        self.__base_local_dir_path = base_local_dir_path

    def set_path_pairs(self, path_pairs: Iterable[_PathPair]):
        self.__path_pairs_by_id = {
            pair.id: {
                "name": pair.name,
                "remote_path": pair.remote_path,
                "local_path": pair.local_path
            } for pair in path_pairs
        }

    def raise_pending_error(self):
        """
        Raise any pending errors
        Errors show up late after a command is executed
        This method raises any errors that were detected while executing the next command
        :return:
        """
        if self.__pending_error:
            error = self.__pending_error
            self.__pending_error = None
            raise LftpError(error)

    @staticmethod
    def __detect_ssh_host_key_prompt(out: str) -> bool:
        prompt_fragments = [
            "The authenticity of host ",
            "Are you sure you want to continue connecting (yes/no",
        ]
        return any(fragment in out for fragment in prompt_fragments)

    def __raise_lftp_error_for_ssh_host_key_prompt(self, out: str, context: str) -> bool:
        if self.__detect_ssh_host_key_prompt(out):
            error = "Lftp stalled on SSH host-key prompt during {}: {}".format(context, out)
            self.logger.error(error)
            raise LftpError(error)
        return False

    @staticmethod
    def __normalize_output(out: str) -> str:
        # lftp in an interactive PTY can leak bracketed-paste toggle lines into command output.
        bracketed_paste_toggle_lines = {
            "\x1b[?2004h",
            "\x1b[?2004l",
        }
        lines = [
            line for line in out.splitlines()
            if line.strip() not in bracketed_paste_toggle_lines
        ]
        return "\n".join(lines).strip()

    def __ensure_prompt_ready(self, context: str):
        try:
            self.__process.expect(self.__expect_pattern, timeout=1)
        except pexpect.exceptions.TIMEOUT:
            out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
            if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, context):
                self.__process.sendline()
                try:
                    self.__process.expect(self.__expect_pattern, timeout=3)
                except pexpect.exceptions.TIMEOUT:
                    retry_out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(retry_out, context):
                        self.logger.warning("Lftp timeout exception")
                        raise LftpError("Lftp process is not ready for {}: {}".format(context, retry_out))
                except pexpect.exceptions.EOF:
                    retry_out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    self.logger.error("Lftp process died unexpectedly (EOF) before {}".format(context))
                    raise LftpError("Lftp process terminated before {}: {}".format(context, retry_out))
        except pexpect.exceptions.EOF:
            out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
            self.logger.error("Lftp process died unexpectedly (EOF) before {}".format(context))
            raise LftpError("Lftp process terminated before {}: {}".format(context, out))

    def __drain_status_poll_terminal(self) -> Optional[str]:
        """Discard stale PTY output before one authoritative status command.

        The single LFTP executor is the only reader.  A background mirror can
        otherwise fill its terminal between polls; pexpect then searches a
        retained prompt before it reads output produced by the new ``jobs -v``.
        Drain only from the existing owner, with a fixed per-poll cap.  A cap
        result is intentionally unhealthy and sends no command; the next
        scheduled poll resumes draining the same terminal rather than spinning.
        """
        process = self.__process
        remaining = STATUS_POLL_TERMINAL_DRAIN_MAX_BYTES
        evidence_tail = ""
        saw_backend_error = False
        saw_host_key_prompt = False
        saw_queue_done = False
        saw_job_or_progress = False
        saw_prompt_or_echo = False
        drained_byte_count = 0
        diagnostic_enabled = _incoming_recovery_diagnostic_enabled()

        def observe(value: object) -> None:
            nonlocal evidence_tail, saw_backend_error, saw_host_key_prompt, saw_queue_done, \
                saw_job_or_progress, saw_prompt_or_echo, drained_byte_count
            if not isinstance(value, (str, bytes)):
                return
            text = self.__decode_spawn_output(value)
            # Detect first so a known marker at the head of a large chunk is
            # not hidden by the bounded cross-chunk tail retained below.
            combined = evidence_tail + text
            saw_host_key_prompt = saw_host_key_prompt or self.__detect_ssh_host_key_prompt(combined)
            saw_backend_error = saw_backend_error or self.__detect_errors_from_output(combined)
            # Keep only enough private in-memory context to recognize an
            # existing error across a chunk boundary.  Never log this output.
            evidence_tail = combined[-1024:]
            if not diagnostic_enabled:
                return
            try:
                drained_byte_count += len(text.encode("utf-8", "surrogateescape"))
            except UnicodeEncodeError:
                drained_byte_count = -1
            lines = [line.strip() for line in combined.splitlines() if line.strip()]
            saw_queue_done = saw_queue_done or any(
                re.match(r"^\[\d+\]\s+Done\s+\(queue\s+\(", line) for line in lines
            )
            saw_job_or_progress = saw_job_or_progress or any(
                re.match(r"^\[\d+\]\s+(?:mirror|pget|get|put)\b", line) or
                re.search(r"\b\d+/\d+\s+\(\d+%\)", line) for line in lines
            )
            saw_prompt_or_echo = saw_prompt_or_echo or "jobs -v" in combined or bool(
                re.search(self.__expect_pattern, combined)
            )

        def finish(failure: Optional[str]) -> Optional[str]:
            if diagnostic_enabled:
                observation = getattr(self, "_Lftp__last_incoming_recovery_status_observation", None)
                observation = dict(observation) if isinstance(observation, dict) else {}
                previous_bytes = observation.get("_pre_send_drain_bytes", 0)
                total_bytes = previous_bytes + drained_byte_count \
                    if type(previous_bytes) is int and previous_bytes >= 0 and drained_byte_count >= 0 else -1
                observation.update({
                    "_pre_send_drain_bytes": total_bytes,
                    "_pre_send_drain_queue_done": bool(observation.get("_pre_send_drain_queue_done")) or saw_queue_done,
                    "_pre_send_drain_job_or_progress": bool(observation.get("_pre_send_drain_job_or_progress")) or saw_job_or_progress,
                    "_pre_send_drain_prompt_or_echo": bool(observation.get("_pre_send_drain_prompt_or_echo")) or saw_prompt_or_echo,
                    "_pre_send_drain_error": bool(observation.get("_pre_send_drain_error")) or
                    saw_backend_error or saw_host_key_prompt,
                })
                self.__last_incoming_recovery_status_observation = observation
            return failure

        def terminal_error() -> Optional[str]:
            if saw_host_key_prompt:
                self.__pending_error = "Lftp status terminal reported a backend error"
                return "command_error"
            if saw_backend_error:
                # Keep the established redacted pending-error contract even
                # though this boundary intentionally discards the raw frame.
                self.logger.error("Lftp status terminal reported a backend error")
                self.__pending_error = "Lftp status terminal reported a backend error"
                return "command_error"
            return None

        def replace_retained(value: Union[str, bytes]) -> None:
            """Keep pexpect's public and private retained input state aligned."""
            buffer_type = self.__process.buffer_type
            buffer = buffer_type()
            buffer.write(value)
            self.__process._buffer = buffer
            before = buffer_type()
            before.write(value)
            self.__process._before = before

        # ``expect`` searches its retained buffer before it reads the PTY.
        # Preserve ``before``: a prior timed status poll deliberately keeps a
        # partial Connecting/error response there for the existing recovery
        # path.  A matched prompt is retained in ``after`` and trailing output
        # in ``buffer``, so drain only that stale boundary plus the PTY fd.
        retained = getattr(process, "_buffer", None)
        buffered = retained.getvalue() if hasattr(retained, "getvalue") else ""
        if isinstance(buffered, (str, bytes)):
            consumed = buffered[:remaining]
            observe(consumed)
            remaining -= len(consumed)
            replace_retained(buffered[len(consumed):])
        else:
            replace_retained(process.buffer_type().getvalue())
        process.after = None
        if terminal_error() is not None:
            return finish("command_error")
        if remaining <= 0:
            return finish("terminal_backlog")

        while remaining > 0:
            try:
                chunk = process.read_nonblocking(
                    size=min(STATUS_POLL_TERMINAL_DRAIN_READ_BYTES, remaining), timeout=0,
                )
            except pexpect.exceptions.TIMEOUT:
                return finish(terminal_error())
            except pexpect.exceptions.EOF:
                return finish("eof")
            if not isinstance(chunk, (str, bytes)) or not chunk:
                return finish(terminal_error())
            observe(chunk)
            remaining -= len(chunk)
            error = terminal_error()
            if error is not None:
                return finish(error)
        return finish("terminal_backlog")

    @with_check_process
    def __run_command(self,
                      command: str,
                      timeout_seconds: Optional[int] = None,
                      require_prompt_ready: bool = True,
                      status_poll: bool = False,
                      low_latency: bool = False,
                      trace_command_kind: Optional[str] = None,
                      trace_flow_id: Optional[str] = None,
                      trace_command_metrics: Optional[tuple[int, int, int]] = None,
                      trace_status_poll_correlation: Optional[str] = None,
                      trace_pty_correlation: Optional[str] = None,
                      diagnostic_recorder: object = None) -> str:
        self.__last_command_timed_out = False
        restore_delaybeforesend = None
        restore_delayafterread = None
        out = ""
        status_poll_timeout_seconds = None
        log_command_output = self.__log_command_output and not status_poll
        command_trace_enabled = trace_command_kind in LFTP_COMMAND_TRACE_KINDS
        safe_status_poll_correlation = _safe_lftp_status_poll_correlation(trace_status_poll_correlation)
        pty_trace = getattr(self, "_Lftp__breadcrumb_trace", None)
        pty_flow_id = _safe_lftp_pty_correlation(trace_pty_correlation)
        diagnostic_started_at = time.monotonic() if _incoming_recovery_diagnostic_enabled() and \
            callable(diagnostic_recorder) else None
        try:
            diagnostic_alive_before = self.__process.isalive() if diagnostic_started_at is not None else None
        except Exception:
            diagnostic_alive_before = None

        def record_diagnostic_child(
                error: object = None, classification: str = "unknown", phase: str = "unknown",
        ) -> None:
            _incoming_recovery_child_record(
                diagnostic_recorder, self.__process, error, classification,
                phase=phase, started_at=diagnostic_started_at,
                alive_before=diagnostic_alive_before,
                command_kind="status" if status_poll else "queue",
            )

        pty_debug_enabled = pty_flow_id is not None and _lftp_pty_trace_enabled(pty_trace, "debug")
        pty_warning_enabled = pty_flow_id is not None and _lftp_pty_trace_enabled(pty_trace, "warning")
        pty_readiness = "not_required" if not require_prompt_ready else "unknown"
        pty_send_mode = "send" if status_poll else "sendline"

        def record_pty_trace(phase: str, outcome: str = "not_attempted", error: object = None) -> None:
            if pty_flow_id is None or not (pty_debug_enabled or pty_warning_enabled):
                return
            try:
                alive = self.__process.isalive()
            except Exception:
                alive = False
            _record_lftp_pty_breadcrumb(
                pty_trace, pty_flow_id, phase, readiness=pty_readiness, send_mode=pty_send_mode,
                prompt_outcome=outcome, process_alive=alive, error=error,
            )

        def record_command_trace(phase: str, output: object = None) -> None:
            if not command_trace_enabled:
                return
            try:
                process_alive = self.__process.isalive()
            except Exception:
                process_alive = False
            _record_lftp_command_breadcrumb(
                getattr(self, "_Lftp__breadcrumb_trace", None), trace_command_kind,
                phase, trace_flow_id, process_alive=process_alive, output=output,
                command_metrics=trace_command_metrics,
            )

        def record_status_trace(phase: str, output: object = None,
                                failure_reason: object = None, status_count: object = None,
                                exception: object = None, healthy: object = None,
                                boundary: object = None, boundary_state: object = None) -> None:
            if safe_status_poll_correlation is None:
                return
            trace = getattr(self, "_Lftp__breadcrumb_trace", None)
            level = "warning" if phase in LFTP_STATUS_POLL_TRACE_FAILURE_PHASES or \
                (phase == "health" and healthy is False) else "debug"
            if not _breadcrumb_effectively_enabled(trace, LFTP_STATUS_POLL_TRACE_CATEGORY, level):
                return
            try:
                process_alive = self.__process.isalive()
            except Exception:
                process_alive = False
            _record_lftp_status_poll_breadcrumb(
                trace, safe_status_poll_correlation, phase,
                process_alive=process_alive, output=output, failure_reason=failure_reason,
                status_count=status_count, exception=exception, healthy=healthy,
                boundary=boundary, boundary_state=boundary_state,
            )
        if status_poll:
            status_poll_timeout_seconds = STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS if timeout_seconds == 0 else timeout_seconds
        if status_poll or low_latency:
            restore_delaybeforesend = getattr(self.__process, "delaybeforesend", None)
            restore_delayafterread = getattr(self.__process, "delayafterread", None)
            if restore_delaybeforesend is not None:
                self.__process.delaybeforesend = 0
            if restore_delayafterread is not None:
                self.__process.delayafterread = 0
        try:
            if require_prompt_ready:
                self.__ensure_prompt_ready("running command")
                pty_readiness = "ready"
            if status_poll:
                terminal_failure = self.__drain_status_poll_terminal()
                if terminal_failure is not None:
                    record_diagnostic_child(classification=terminal_failure, phase="drain")
                    self.__last_command_timed_out = True
                    self.__last_status_poll_failure_reason = terminal_failure
                    record_status_trace(
                        "terminal_backlog" if terminal_failure == "terminal_backlog" else
                        ("process_eof" if terminal_failure == "eof" else "command_error"),
                        failure_reason=terminal_failure,
                        boundary="read",
                        boundary_state={
                            "prior_prompt": pty_readiness,
                            "send_admitted": False,
                            "prompt_reached": "unknown",
                            "retained_before": "unknown",
                        },
                    )
                    return ""
            if pty_debug_enabled:
                record_pty_trace("pre_write")
            if log_command_output:
                self.logger.debug("command: {}".format(command.encode('utf8', 'surrogateescape')))
            try:
                if status_poll:
                    self.__process.send(command + "\n")
                else:
                    self.__process.sendline(command)
                if pty_debug_enabled:
                    record_pty_trace("write")
                record_command_trace("submitted")
                record_status_trace("submitted", boundary_state={
                    "prior_prompt": pty_readiness,
                    "send_admitted": True,
                    "prompt_reached": "unknown",
                    "retained_before": "unknown",
                })
            except pexpect.exceptions.TIMEOUT as exc:
                if status_poll:
                    record_diagnostic_child(exc, phase="send")
                record_pty_trace("write", "send_error", exc)
                record_command_trace("prompt_timeout")
                record_status_trace("prompt_timeout", failure_reason="timeout", exception=exc, boundary="send")
                if status_poll:
                    self.__last_command_timed_out = True
                    self.__last_status_poll_failure_reason = "timeout"
                    self.logger.warning("Lftp timeout exception")
                    return ""
                raise
            except pexpect.exceptions.EOF as exc:
                if status_poll:
                    record_diagnostic_child(exc, phase="send")
                record_pty_trace("write", "send_error", exc)
                record_command_trace("process_eof")
                record_status_trace("process_eof", failure_reason="eof", exception=exc, boundary="send")
                if status_poll:
                    self.__last_command_timed_out = True
                    self.__last_status_poll_failure_reason = "eof"
                    self.logger.error("Lftp process died unexpectedly (EOF) while sending status command")
                    return ""
                raise
            except Exception as exc:
                record_pty_trace("write", "send_error", exc)
                raise
            timeout_seconds = self.__timeout if timeout_seconds is None else timeout_seconds
            prompt_reached = False
            final_prompt_reached = False
            recovered_output_preserved = False
            try:
                if status_poll:
                    try:
                        status_poll_timeout_seconds = STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS if timeout_seconds == 0 else timeout_seconds
                        status_poll_deadline = time.monotonic() + status_poll_timeout_seconds
                        while True:
                            try:
                                self.__process.expect(self.__expect_pattern, timeout=0)
                                prompt_reached = True
                                final_prompt_reached = True
                                break
                            except pexpect.exceptions.TIMEOUT:
                                if time.monotonic() >= status_poll_deadline:
                                    break
                                time.sleep(0.01)
                            except pexpect.exceptions.EOF as exc:
                                record_diagnostic_child(exc, phase="prompt")
                                self.__last_command_timed_out = True
                                self.__last_status_poll_failure_reason = "eof"
                                record_status_trace("process_eof", failure_reason="eof", exception=exc)
                                self.logger.error("Lftp process died unexpectedly (EOF)")
                                raise LftpError("Lftp process terminated: {}".format(
                                    self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                                ))
                    except pexpect.exceptions.ExceptionPexpect as exc:
                        record_diagnostic_child(exc, "command_error", "prompt")
                        self.__last_command_timed_out = True
                        self.__last_status_poll_failure_reason = "command_error"
                        record_status_trace("command_error", failure_reason="command_error", exception=exc)
                        self.logger.warning("Ignoring status poll failure: {}".format(exc))
                        return ""
                    except OSError as exc:
                        record_diagnostic_child(exc, "command_error", "prompt")
                        self.__last_command_timed_out = True
                        self.__last_status_poll_failure_reason = "command_error"
                        record_status_trace("command_error", failure_reason="command_error", exception=exc)
                        self.logger.warning("Ignoring status poll failure: {}".format(exc))
                        return ""
                    if not prompt_reached:
                        self.__last_command_timed_out = True
                        self.__last_status_poll_failure_reason = "timeout"
                else:
                    try:
                        self.__process.expect(self.__expect_pattern, timeout=timeout_seconds)
                        record_pty_trace("prompt", "prompt_ready")
                        record_command_trace("prompt_ready")
                    except pexpect.exceptions.TIMEOUT as exc:
                        self.__last_command_timed_out = True
                        out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                        record_pty_trace("prompt", "prompt_timeout", exc)
                        record_command_trace("prompt_timeout", out)
                        if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "running command"):
                            self.logger.warning("Lftp timeout exception")
                        pass
                    except pexpect.exceptions.EOF as exc:
                        record_pty_trace("prompt", "process_eof", exc)
                        record_command_trace("process_eof")
                        self.logger.error("Lftp process died unexpectedly (EOF)")
                        raise LftpError("Lftp process terminated: {}".format(
                            self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                        ))
            finally:
                out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))

                if log_command_output:
                    if status_poll:
                        self.logger.debug("status out ({} bytes, bounded)".format(len(out)))
                    else:
                        self.logger.debug("out ({} bytes):\n {}".format(len(out), redact_credentials(out)))
                    after = self.__decode_spawn_output(self.__process.after).strip()
                    self.logger.debug("after: {}".format(after))

            if status_poll:
                record_status_trace("jobs_read", out, boundary_state={
                    "prior_prompt": pty_readiness,
                    "send_admitted": True,
                    "prompt_reached": prompt_reached,
                    "retained_before": "unknown",
                })
                if prompt_reached:
                    record_status_trace("prompt_ready", out)
                else:
                    record_diagnostic_child(classification="timeout", phase="prompt")
                    record_status_trace("prompt_timeout", out, failure_reason="timeout")

            if status_poll and "Connecting..." in out:
                self.__status_poll_needs_connection_grace = True
            if status_poll and not prompt_reached and "Connecting..." in out:
                recovered_output_preserved = True
                try:
                    connecting_grace_timeout = max(status_poll_timeout_seconds or 0, 5.0)
                    self.__process.expect(self.__expect_pattern, timeout=connecting_grace_timeout)
                    final_prompt_reached = True
                    record_status_trace("prompt_ready")
                except pexpect.exceptions.TIMEOUT:
                    record_diagnostic_child(classification="timeout", phase="connecting")
                    pass
                except pexpect.exceptions.EOF as exc:
                    record_diagnostic_child(exc, phase="connecting")
                    self.__last_command_timed_out = True
                    self.__last_status_poll_failure_reason = "eof"
                    record_status_trace("process_eof", failure_reason="eof", exception=exc)
                    self.logger.error("Lftp process died unexpectedly (EOF) during status poll recovery")
                    raise LftpError("Lftp process terminated during status poll recovery")
                finally:
                    out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))

            if status_poll and not prompt_reached and not recovered_output_preserved and not self.__detect_errors_from_output(out):
                out = ""
            if status_poll and _incoming_recovery_diagnostic_enabled():
                observation = getattr(self, "_Lftp__last_incoming_recovery_status_observation", None)
                observation = dict(observation) if isinstance(observation, dict) else {}
                observation["_post_send_prompt"] = "reached" if final_prompt_reached else "not_reached"
                self.__last_incoming_recovery_status_observation = observation

            # let's try and detect some errors
            if self.__detect_errors_from_output(out):
                record_command_trace("backend_error", out)
                record_status_trace("backend_error", out, failure_reason="command_error")
                if status_poll:
                    record_diagnostic_child(classification="command_error", phase="error_recovery")
                # we need to consume the actual output so that
                # it doesn't get passed onto next command
                error_out = out
                try:
                    self.__process.expect(self.__expect_pattern, timeout=timeout_seconds)
                except pexpect.exceptions.TIMEOUT:
                    if status_poll:
                        record_diagnostic_child(classification="timeout", phase="error_recovery")
                    out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    if not self.__raise_lftp_error_for_ssh_host_key_prompt(out, "recovering from error"):
                        self.logger.warning("Lftp timeout exception")
                    pass
                except pexpect.exceptions.EOF as exc:
                    if status_poll:
                        record_diagnostic_child(exc, phase="error_recovery")
                        self.__last_status_poll_failure_reason = "eof"
                    self.logger.error("Lftp process died unexpectedly (EOF) during error recovery")
                    raise LftpError("Lftp process terminated during error recovery")
                finally:
                    out = self.__normalize_output(self.__decode_spawn_output(self.__process.before))
                    if log_command_output:
                        self.logger.debug("retry out ({} bytes):\n {}".format(len(out), redact_credentials(out)))
                        after = self.__decode_spawn_output(self.__process.after).strip()
                        self.logger.debug("retry after: {}".format(after))
                    redacted_error_out = redact_credentials(error_out)
                    self.logger.error("Lftp detected error: {}".format(redacted_error_out))
                    # save pending error
                    self.__pending_error = redacted_error_out
            return out
        finally:
            if restore_delaybeforesend is not None:
                self.__process.delaybeforesend = restore_delaybeforesend
            if restore_delayafterread is not None:
                self.__process.delayafterread = restore_delayafterread

    @staticmethod
    def __detect_errors_from_output(out: str) -> bool:
        errors = [
            "get: Access failed",
            "pget: Access failed",
            "pget-chunk: Access failed",
            "mirror: Access failed",
            "Login failed:",
            "Fatal error: gnutls_handshake",
            "Fatal error: SSL_connect",
            "TLS/SSL connection error",
            "Certificate verification",
            "certificate verification",
            "AUTH TLS failed",
        ]
        for error in errors:
            if error in out:
                return True
        return False

    def __set(self, setting: str, value: str):
        """
        Set a setting in the lftp runtime
        :param setting:
        :param value:
        :return:
        """
        self.__run_command("set {} {}".format(setting, value), require_prompt_ready=False)  # type: ignore[arg-type]

    def __get(self, setting: str) -> str:
        """
        Get a setting from the lftp runtime
        :param setting:
        :return:
        """
        out = self.__run_command("set -a | grep {}".format(setting))  # type: ignore[arg-type]
        m = re.search(r"^set {} (.*)$".format(re.escape(setting)), out, re.MULTILINE)
        if not m or not m.group(1):
            raise LftpError("Failed to get setting '{}'. Output: '{}'".format(setting, out))
        return m.group(1).strip()

    @staticmethod
    def __to_bool(value: str) -> bool:
        # sets are taken from LFTP manual
        if value.lower() in {"true", "on", "yes", "1", "+"}:
            return True
        elif value.lower() in {"false",  "off", "no", "0", "-"}:
            return False
        else:
            raise LftpError("Cannot convert value '{}' to boolean".format(value))

    @property
    def num_connections_per_dir_file(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_CONNECTIONS_MIRROR))

    @num_connections_per_dir_file.setter
    def num_connections_per_dir_file(self, num_connections: int):
        if num_connections < 1:
            raise ValueError("Number of connections must be positive")
        self.__set(Lftp.__SET_NUM_CONNECTIONS_MIRROR, str(num_connections))

    @property
    def num_connections_per_root_file(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_CONNECTIONS_PGET))

    @num_connections_per_root_file.setter
    def num_connections_per_root_file(self, num_connections: int):
        if num_connections < 1:
            raise ValueError("Number of connections must be positive")
        self.__set(Lftp.__SET_NUM_CONNECTIONS_PGET, str(num_connections))

    @property
    def num_max_total_connections(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_MAX_TOTAL_CONNECTIONS))

    @num_max_total_connections.setter
    def num_max_total_connections(self, num_connections: int):
        if num_connections < 0:
            raise ValueError("Number of connections must be zero or greater")
        self.__set(Lftp.__SET_NUM_MAX_TOTAL_CONNECTIONS, str(num_connections))

    @property
    def num_parallel_files(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_PARALLEL_FILES))

    @num_parallel_files.setter
    def num_parallel_files(self, num_parallel_files: int):
        if num_parallel_files < 1:
            raise ValueError("Number of parallel files must be positive")
        self.__set(Lftp.__SET_NUM_PARALLEL_FILES, str(num_parallel_files))

    @property
    def rate_limit(self) -> str:
        return self.__get(Lftp.__SET_RATE_LIMIT)

    @rate_limit.setter
    def rate_limit(self, rate_limit: Union[int, str]):
        self.__set(Lftp.__SET_RATE_LIMIT, str(rate_limit))

    @property
    def net_socket_buffer(self) -> str:
        return self.__get(Lftp.__SET_NET_SOCKET_BUFFER)

    @net_socket_buffer.setter
    def net_socket_buffer(self, net_socket_buffer: Union[int, str]):
        normalized = Checkers.byte_size_or_empty(Lftp, "net_socket_buffer", net_socket_buffer)
        if normalized == "":
            return
        self.__set(Lftp.__SET_NET_SOCKET_BUFFER, normalized)

    @property
    def min_chunk_size(self) -> str:
        return self.__get(Lftp.__SET_MIN_CHUNK_SIZE)

    @min_chunk_size.setter
    def min_chunk_size(self, min_chunk_size: Union[int, str]):
        self.__set(Lftp.__SET_MIN_CHUNK_SIZE, str(min_chunk_size))

    @property
    def num_parallel_jobs(self) -> int:
        return int(self.__get(Lftp.__SET_NUM_PARALLEL_JOBS))

    @num_parallel_jobs.setter
    def num_parallel_jobs(self, num_parallel_jobs: int):
        if num_parallel_jobs < 1:
            raise ValueError("Number of parallel jobs must be positive")
        self.__set(Lftp.__SET_NUM_PARALLEL_JOBS, str(num_parallel_jobs))

    @property
    def move_background_on_exit(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_MOVE_BACKGROUND_ON_EXIT))

    @move_background_on_exit.setter
    def move_background_on_exit(self, move_background_on_exit: bool):
        self.__set(Lftp.__SET_MOVE_BACKGROUND_ON_EXIT, str(int(move_background_on_exit)))

    @property
    def use_temp_file(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_USE_TEMP_FILE))

    @use_temp_file.setter
    def use_temp_file(self, use_temp_file: bool):
        self.__set(Lftp.__SET_USE_TEMP_FILE, str(int(use_temp_file)))

    @property
    def xfer_verify(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_XFER_VERIFY))

    @xfer_verify.setter
    def xfer_verify(self, value: bool):
        self.__set(Lftp.__SET_XFER_VERIFY, str(int(value)))

    @property
    def xfer_verify_command(self) -> str:
        return self.__get(Lftp.__SET_XFER_VERIFY_COMMAND)

    @xfer_verify_command.setter
    def xfer_verify_command(self, command: str):
        self.__set(Lftp.__SET_XFER_VERIFY_COMMAND, command)

    @property
    def temp_file_name(self) -> str:
        return self.__get(Lftp.__SET_TEMP_FILE_NAME)

    @temp_file_name.setter
    def temp_file_name(self, temp_file_name: str):
        self.__set(Lftp.__SET_TEMP_FILE_NAME, temp_file_name)

    @property
    def sftp_auto_confirm(self) -> bool:
        return Lftp.__to_bool(self.__get(Lftp.__SET_SFTP_AUTO_CONFIRM))

    @sftp_auto_confirm.setter
    def sftp_auto_confirm(self, auto_confirm: bool):
        self.__set(Lftp.__SET_SFTP_AUTO_CONFIRM, str(int(auto_confirm)))

    @property
    def last_status_poll_healthy(self) -> bool:
        return self.__last_status_poll_healthy

    @property
    def last_command_timed_out(self) -> bool:
        """Expose the most recent command's prompt outcome for opt-in diagnostics only."""
        return self.__last_command_timed_out

    @property
    def last_status_poll_failure_reason(self) -> Optional[str]:
        """Return the fixed safe category for the most recent status poll."""
        reason = getattr(self, "_Lftp__last_status_poll_failure_reason", None)
        return reason if reason in LFTP_STATUS_POLL_FAILURE_REASONS else None

    @property
    def incoming_recovery_last_status_observation(self) -> dict[str, object]:
        """Return bounded last-poll metadata for the exact-600 root record only."""
        observation = getattr(self, "_Lftp__last_incoming_recovery_status_observation", None)
        if not isinstance(observation, dict):
            return {}
        fields = {
            "status_result_shape", "status_recovery", "status_preceded_timeout",
            "pre_send_drain_shape", "pre_send_drain_byte_length_bucket",
            "pre_send_drain_queue_done", "pre_send_drain_job_or_progress",
            "pre_send_drain_prompt_or_echo", "pre_send_drain_error", "post_send_prompt",
            "process_pid", "process_alive", "read_buffer_source", "read_buffer_byte_length_bucket",
        }
        return {field: observation[field] for field in fields if field in observation}

    def __record_incoming_recovery_status_observation(
            self, output: object, statuses: object, *, preceded_timeout: object,
            used_connection_grace: object,
    ) -> None:
        if not _incoming_recovery_diagnostic_enabled():
            return
        try:
            process = self.__process
            alive = process.isalive()
            pid = getattr(process, "pid", None)
        except Exception:
            process = None
            alive = None
            pid = None
        buffer_source, buffer_bucket = _incoming_recovery_buffer_observation(process)
        result_shape = _incoming_recovery_status_result_shape(output, statuses)
        recovery = "connection_grace" if used_connection_grace is True else \
            "none" if used_connection_grace is False else "unknown"
        prior = getattr(self, "_Lftp__last_incoming_recovery_status_observation", None)
        prior = prior if isinstance(prior, dict) else {}
        drain_bytes = prior.get("_pre_send_drain_bytes", -1)
        drain_marked = any(prior.get(field) is True for field in (
            "_pre_send_drain_queue_done", "_pre_send_drain_job_or_progress",
            "_pre_send_drain_prompt_or_echo", "_pre_send_drain_error",
        ))
        self.__last_incoming_recovery_status_observation = {
            "status_result_shape": result_shape if result_shape in _INCOMING_RECOVERY_STATUS_RESULT_SHAPES else "unknown",
            "status_recovery": recovery if recovery in _INCOMING_RECOVERY_STATUS_RECOVERIES else "unknown",
            "status_preceded_timeout": preceded_timeout if type(preceded_timeout) is bool else None,
            "pre_send_drain_shape": "empty" if drain_bytes == 0 else "marked" if drain_marked else "unknown",
            "pre_send_drain_byte_length_bucket": _lftp_trace_bytes_bucket(drain_bytes),
            "pre_send_drain_queue_done": prior.get("_pre_send_drain_queue_done") is True,
            "pre_send_drain_job_or_progress": prior.get("_pre_send_drain_job_or_progress") is True,
            "pre_send_drain_prompt_or_echo": prior.get("_pre_send_drain_prompt_or_echo") is True,
            "pre_send_drain_error": prior.get("_pre_send_drain_error") is True,
            "post_send_prompt": prior.get("_post_send_prompt")
            if prior.get("_post_send_prompt") in {"reached", "not_reached"} else "unknown",
            "process_pid": pid if type(pid) is int and pid > 0 else "unavailable",
            "process_alive": alive if type(alive) is bool else None,
            "read_buffer_source": buffer_source,
            "read_buffer_byte_length_bucket": buffer_bucket,
        }

    def __record_incoming_recovery_status_completion(
            self, diagnostic_recorder: object, statuses: object,
    ) -> None:
        """Bind one completed post-Queue status poll to the existing active flow."""
        if not _incoming_recovery_diagnostic_enabled() or not callable(diagnostic_recorder) or \
                not isinstance(statuses, list) or self.__last_status_poll_healthy is not True or \
                self.__last_status_poll_failure_reason is not None:
            return
        try:
            observation = self.incoming_recovery_last_status_observation
            healthy = self.__last_status_poll_healthy
            status_count = len(statuses)
            status_count_bucket = (
                "0" if status_count == 0 else "1" if status_count == 1 else
                "2-4" if status_count <= 4 else "5-16" if status_count <= 16 else "17+"
            )
            diagnostic_recorder("lftp_child", {
                "phase": "status", "command_kind": "status",
                "classification": "none", "status_health": "healthy" if healthy is True else "unknown",
                "status_count_bucket": status_count_bucket,
                **observation,
            })
        except Exception:
            # The existing diagnostic callback is never part of status ownership.
            return

    @property
    def sftp_connect_program(self) -> str:
        return self.__get(Lftp.__SET_SFTP_CONNECT_PROGRAM)

    @sftp_connect_program.setter
    def sftp_connect_program(self, program: str):
        self.__set(Lftp.__SET_SFTP_CONNECT_PROGRAM, program)

    def status(self, trace_poll_correlation: Optional[str] = None,
               diagnostic_recorder: object = None) -> Optional[List[LftpJobStatus]]:
        """
        Return a status list of queued and running jobs, or None when
        parsing failed but the error is still within the tolerated threshold.
        :return:
        """
        self.__last_status_poll_failure_reason = None
        # A prior exact-600 poll must never be associated with this poll, or
        # survive after the gate is disabled.
        self.__last_incoming_recovery_status_observation = None
        safe_trace_poll_correlation = _safe_lftp_status_poll_correlation(trace_poll_correlation)

        def record_status_result(phase: str, output: object = None, status_count: object = None,
                                 exception: object = None, healthy: object = None,
                                 failure_reason: object = None) -> None:
            if safe_trace_poll_correlation is None:
                return
            trace = getattr(self, "_Lftp__breadcrumb_trace", None)
            level = "warning" if phase in LFTP_STATUS_POLL_TRACE_FAILURE_PHASES or \
                (phase == "health" and healthy is False) else "debug"
            if not _breadcrumb_effectively_enabled(trace, LFTP_STATUS_POLL_TRACE_CATEGORY, level):
                return
            try:
                process_alive = self.__process.isalive()
            except Exception:
                process_alive = False
            _record_lftp_status_poll_breadcrumb(
                trace, safe_trace_poll_correlation, phase,
                process_alive=process_alive, output=output,
                failure_reason=(self.__last_status_poll_failure_reason
                                if failure_reason is None else failure_reason),
                status_count=status_count, exception=exception, healthy=healthy,
            )

        def capture_private_frame(output: object, process_alive: object) -> None:
            process = getattr(self, "_Lftp__process", None)
            _lftp_private_status_frame_capture(
                safe_trace_poll_correlation, output, process_alive,
                getattr(self, "_Lftp__breadcrumb_trace", None),
                {
                    "pexpect_version": str(getattr(pexpect, "__version__", "unknown")),
                    "command_timed_out": self.__last_command_timed_out is True,
                    "before_byte_count": _lftp_boundary_byte_count(getattr(process, "before", None)),
                    "after_byte_count": _lftp_boundary_byte_count(getattr(process, "after", None)),
                    "buffer_byte_count": _lftp_boundary_byte_count(getattr(process, "buffer", None)),
                },
            )
        try:
            status_command_kwargs: dict[str, object] = {
                "timeout_seconds": 0,
                "require_prompt_ready": False,
                "status_poll": True,
            }
            if safe_trace_poll_correlation is not None:
                status_command_kwargs["trace_status_poll_correlation"] = safe_trace_poll_correlation
            if _incoming_recovery_diagnostic_enabled() and callable(diagnostic_recorder):
                status_command_kwargs["diagnostic_recorder"] = diagnostic_recorder
            out = self.__run_command("jobs -v", **status_command_kwargs)  # type: ignore[arg-type]
        except pexpect.exceptions.TIMEOUT as exc:
            self.__consecutive_status_errors = 0
            self.__last_command_timed_out = True
            self.__last_status_poll_failure_reason = "timeout"
            self.__last_status_poll_healthy = False
            record_status_result("prompt_timeout", exception=exc)
            record_status_result("health", healthy=False, exception=exc)
            self.logger.warning("Lftp timeout exception")
            return []
        except pexpect.exceptions.EOF as exc:
            self.__consecutive_status_errors = 0
            self.__last_command_timed_out = True
            self.__last_status_poll_failure_reason = "eof"
            self.__last_status_poll_healthy = False
            record_status_result("process_eof", exception=exc)
            record_status_result("health", healthy=False, exception=exc)
            self.logger.error("Lftp process died unexpectedly (EOF) during status poll")
            return []
        except LftpError as exc:
            self.__consecutive_status_errors = 0
            self.__last_command_timed_out = True
            if self.__last_status_poll_failure_reason not in {"eof", "timeout"}:
                self.__last_status_poll_failure_reason = "command_error"
            self.__last_status_poll_healthy = False
            record_status_result("command_error", exception=exc)
            record_status_result("health", healthy=False, exception=exc)
            self.logger.warning("Ignoring status poll failure: {}".format(exc))
            return []
        except Exception as exc:
            # Preserve unexpected status-worker propagation while leaving a
            # bounded, type-only breadcrumb for the independent poll lineage.
            record_status_result(
                "health", healthy=False, exception=exc,
                failure_reason="unhealthy_snapshot",
            )
            raise
        timed_out = self.__last_command_timed_out
        preceded_timeout = timed_out is True
        used_connection_grace = False
        statuses: Optional[List[LftpJobStatus]] = None
        try:
            try:
                preparse_process_alive = self.__process.isalive()
            except Exception:
                preparse_process_alive = False
            _record_lftp_preparse_stderr(
                safe_trace_poll_correlation, out, preparse_process_alive,
                getattr(self, "_Lftp__breadcrumb_trace", None),
            )
            record_status_result("parse_started", out)
            statuses = self.__job_status_parser.parse(out)
            self.__consecutive_status_errors = 0
            self.__last_status_poll_healthy = not timed_out
            record_status_result("parse_complete", out, len(statuses))
        except LftpJobStatusParserError as exc:
            self.__consecutive_status_errors += 1
            self.__last_status_poll_failure_reason = "parser_error"
            self.__last_status_poll_healthy = False
            capture_private_frame(out, preparse_process_alive)
            record_status_result("parse_error", out, exception=exc)
            if self.__consecutive_status_errors < MAX_CONSECUTIVE_STATUS_ERRORS:
                self.logger.warning(f"Ignoring status error (count={self.__consecutive_status_errors})")
            else:
                record_status_result("health", healthy=False, exception=exc)
                raise
        if statuses is not None:
            self.__annotate_status_path_pairs(statuses)
        if not statuses and getattr(self, "_Lftp__status_poll_needs_connection_grace", False) and not self.__pending_error:
            self.__status_poll_needs_connection_grace = False
            used_connection_grace = True
            connection_grace_timeout = max(STATUS_POLL_PROMPT_READY_TIMEOUT_SECONDS, 5.0)
            run_command: Any = self.__run_command
            out = run_command(
                "jobs -v",
                timeout_seconds=connection_grace_timeout,
                require_prompt_ready=False,
                status_poll=True,
                **({"trace_status_poll_correlation": safe_trace_poll_correlation}
                   if safe_trace_poll_correlation is not None else {}),
                **({"diagnostic_recorder": diagnostic_recorder}
                   if _incoming_recovery_diagnostic_enabled() and callable(diagnostic_recorder) else {})
            )
            try:
                try:
                    preparse_process_alive = self.__process.isalive()
                except Exception:
                    preparse_process_alive = False
                _record_lftp_preparse_stderr(
                    safe_trace_poll_correlation, out, preparse_process_alive,
                    getattr(self, "_Lftp__breadcrumb_trace", None),
                )
                record_status_result("parse_started", out)
                statuses = self.__job_status_parser.parse(out)
                self.__consecutive_status_errors = 0
                self.__last_status_poll_healthy = not self.__last_command_timed_out
                record_status_result("parse_complete", out, len(statuses))
            except LftpJobStatusParserError as exc:
                self.__consecutive_status_errors += 1
                self.__last_status_poll_failure_reason = "parser_error"
                self.__last_status_poll_healthy = False
                capture_private_frame(out, preparse_process_alive)
                record_status_result("parse_error", out, exception=exc)
                if self.__consecutive_status_errors < MAX_CONSECUTIVE_STATUS_ERRORS:
                    self.logger.warning(f"Ignoring status error (count={self.__consecutive_status_errors})")
                else:
                    record_status_result("health", healthy=False, exception=exc)
                    raise
            if statuses is not None:
                self.__annotate_status_path_pairs(statuses)
        if not self.__last_status_poll_healthy and self.__last_status_poll_failure_reason is None:
            self.__last_status_poll_failure_reason = "unhealthy_snapshot"
        self.__record_incoming_recovery_status_observation(
            out, statuses, preceded_timeout=preceded_timeout,
            used_connection_grace=used_connection_grace,
        )
        self.__record_incoming_recovery_status_completion(diagnostic_recorder, statuses)
        record_status_result(
            "health", status_count=(len(statuses) if statuses is not None else None),
            healthy=self.__last_status_poll_healthy,
        )
        return statuses

    def __annotate_status_path_pairs(self, statuses: List[LftpJobStatus]):
        if not self.__path_pairs_by_id:
            return
        for status in statuses:
            remote_matches = {
                pair_id for pair_id, pair in self.__path_pairs_by_id.items()
                if Lftp.__path_is_within(status.remote_path, pair["remote_path"])
            }
            local_matches = {
                pair_id for pair_id, pair in self.__path_pairs_by_id.items()
                if Lftp.__path_is_within(status.local_path, pair["local_path"])
            }
            # Recovery resumes individual leaves beneath the staging root,
            # which is intentionally outside the final local Path Pair.
            # A unique remote match still supplies an exact pair-relative
            # identity in that case.  Retain the existing rejection when the
            # local path resolves to another configured pair.
            if len(remote_matches) == 0:
                annotation_result = "unscoped"
                annotation_reason = "remote_no_match"
            elif len(remote_matches) > 1:
                annotation_result = "unscoped"
                annotation_reason = "remote_multiple_matches"
            elif local_matches and remote_matches != local_matches:
                annotation_result = "unscoped"
                annotation_reason = "local_conflict"
            elif not local_matches:
                annotation_result = "selected"
                annotation_reason = "remote_only"
            else:
                annotation_result = "selected"
                annotation_reason = "matched"
            _record_lftp_path_pair_annotation(
                getattr(self, "_Lftp__breadcrumb_trace", None),
                status,
                len(remote_matches),
                len(local_matches),
                annotation_result,
                annotation_reason,
            )
            if annotation_result != "selected":
                continue
            pair_id = next(iter(remote_matches))
            pair = self.__path_pairs_by_id[pair_id]
            status.path_pair_id = pair_id
            status.path_pair_name = pair["name"]
            # Job output names only the basename.  Once a status is proven to
            # belong to one Path Pair, its remote path supplies the canonical
            # pair-relative identity used by the model and controller.
            relative_path = os.path.relpath(
                Lftp.__normalize_path(status.remote_path),
                Lftp.__normalize_path(pair["remote_path"]),
            )
            if relative_path not in ("", ".") and not relative_path.startswith(".." + os.sep):
                status.name = relative_path

    @staticmethod
    def __path_is_within(path: Optional[str], root: str) -> bool:
        if path is None:
            return False
        normalized_path = Lftp.__normalize_path(path)
        normalized_root = Lftp.__normalize_path(root)
        try:
            common = os.path.commonpath([normalized_path, normalized_root])
        except ValueError:
            return False
        return common == normalized_root

    @staticmethod
    def __normalize_path(path: str) -> str:
        return os.path.normpath(path)

    @staticmethod
    def __status_matches_paths(status: LftpJobStatus, remote_root: str, local_root: str) -> bool:
        remote_matches = Lftp.__path_is_within(status.remote_path, remote_root)
        local_matches = Lftp.__path_is_within(status.local_path, local_root)
        return remote_matches and local_matches

    @classmethod
    def __file_resume_artifacts(
            cls, local_dir: str, name: str, expected_size: Optional[int] = None,
            breadcrumb_trace: object = None, flow_id: object = None,
    ) -> tuple[bool, bool]:
        """Return (one target exists, it has a valid matching pget map).

        LFTP's multi-connection pget resume requires the segment map beside the
        target.  This snapshot is intentionally non-mutating: a map is either
        valid and authorizes pget, absent and authorizes contiguous get, or it
        is unsafe and fails before queueing.  Both the direct and historical
        ``.lftp`` forms are inspected so an ambiguous artifact cannot fall
        through to get -c.
        """
        local_root, target_paths = cls.__file_artifact_paths(local_dir, name)
        target_identity = "{}|{}".format(local_dir, name)

        def record(
                classification: str,
                *,
                target_presence: str = "unknown",
                sidecar_presence: str = "unknown",
                sidecar_size: str = "unknown",
        ) -> None:
            _record_lftp_sidecar_breadcrumb(
                breadcrumb_trace,
                classification,
                target_identity,
                target_presence=target_presence,
                sidecar_presence=sidecar_presence,
                sidecar_size=sidecar_size,
                flow_id=flow_id,
            )

        targets: list[tuple[str, Optional[str]]] = []
        status_paths: list[str] = []
        for target_path, status_path in target_paths:
            if not cls.__is_lexically_and_really_contained(target_path, local_root):
                record("unsafe")
                raise LftpError("LFTP queue target is outside the local directory")
            status_paths.append(status_path)
            if not cls.__is_lexically_and_really_contained(status_path, local_root):
                # The only ordinary reason a derived sibling escapes the
                # resolved root is that it is a link/reparse point. Do not
                # follow it or downgrade it to a get resume.
                record("unsafe")
                raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue")
            try:
                target_info = os.stat(target_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                record("unsafe")
                raise LftpError("LFTP queue target is unsafe; use Delete Local before Queue") from error
            if cls.__is_link_or_reparse(target_path, target_info):
                record("unsafe", target_presence="known")
                raise LftpError("LFTP queue target must be a regular file")
            if not stat.S_ISREG(target_info.st_mode):
                record("unsafe", target_presence="known")
                raise LftpError("LFTP queue target must be a regular file")
            try:
                status_info = os.stat(status_path, follow_symlinks=False)
            except FileNotFoundError:
                status_path = None
            except OSError as error:
                record("unsafe", target_presence="known")
                raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue") from error
            targets.append((target_path, status_path))

        for status_path in status_paths:
            if any(target_status_path == status_path for _, target_status_path in targets):
                continue
            try:
                os.stat(status_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                record("unsafe")
                raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue") from error
            record("orphan", target_presence="known", sidecar_presence="known")
            raise LftpError("LFTP queue status sidecar is an orphan; use Delete Local before Queue")

        if not targets:
            record("missing", target_presence="known", sidecar_presence="known")
            return False, False
        if len(targets) != 1:
            record("ambiguous", target_presence="known", sidecar_presence="known")
            raise LftpError("LFTP queue has ambiguous local partial artifacts; use Delete Local before Queue")

        _, status_path = targets[0]
        if status_path is None:
            record("valid", target_presence="known", sidecar_presence="known")
            return True, False
        try:
            status_info = os.stat(status_path, follow_symlinks=False)
        except OSError as error:
            record("unsafe", target_presence="known", sidecar_presence="unknown")
            raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue") from error
        if cls.__is_link_or_reparse(status_path, status_info) or not stat.S_ISREG(status_info.st_mode):
            record("unsafe", target_presence="known", sidecar_presence="known")
            raise LftpError("LFTP queue status sidecar is unsafe; use Delete Local before Queue")
        if not cls.__is_valid_pget_status_file(status_path):
            record("malformed", target_presence="known", sidecar_presence="known")
            raise LftpError("LFTP queue status sidecar is invalid; use Delete Local before Queue")
        if type(expected_size) is int and expected_size >= 0 and \
                cls.__pget_status_file_size(status_path) != expected_size:
            record("stale", target_presence="known", sidecar_presence="known", sidecar_size="unknown")
            raise LftpError("LFTP queue status sidecar is stale; use Delete Local before Queue")
        record("valid", target_presence="known", sidecar_presence="known", sidecar_size="known")
        return True, True

    @classmethod
    def get_safe_file_artifact_delete_paths(cls, local_dir: str, name: str) -> tuple[str, ...]:
        """Return app-owned exact file/map paths that Delete Local may remove.

        This is deliberately distinct from queue resume validation: malformed
        maps are unsafe to resume, but a regular malformed map is safe for the
        user-authorized Delete Local repair to remove.  More than one direct or
        historical-temp form is ambiguous and remains fail-closed.
        """
        cls.__validate_queue_name(name)
        local_root, target_paths = cls.__file_artifact_paths(local_dir, name)
        try:
            root_info = os.lstat(local_root)
        except OSError as error:
            raise LftpError("Delete Local staging directory is unsafe") from error
        if cls.__is_link_or_reparse(local_root, root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise LftpError("Delete Local staging directory is unsafe")

        forms: list[tuple[str, ...]] = []
        for target_path, status_path in target_paths:
            existing: list[str] = []
            for path in (target_path, status_path):
                if not cls.__is_lexically_and_really_contained(path, local_root):
                    raise LftpError("Delete Local staging artifact is unsafe")
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise LftpError("Delete Local staging artifact is unsafe") from error
                if cls.__is_link_or_reparse(path, info) or not stat.S_ISREG(info.st_mode):
                    raise LftpError("Delete Local staging artifact is unsafe")
                existing.append(path)
            if existing:
                forms.append(tuple(existing))
        if len(forms) > 1:
            raise LftpError("Delete Local has ambiguous local partial artifacts")
        return forms[0] if forms else ()

    @classmethod
    def __file_artifact_paths(cls, local_dir: str, name: str) -> tuple[str, tuple[tuple[str, str], ...]]:
        local_root = os.path.abspath(os.path.normpath(local_dir))
        local_name = os.path.join(*name.split("/"))
        targets = (
            os.path.join(local_root, local_name),
            os.path.join(local_root, local_name + cls.__LFTP_TEMP_FILE_SUFFIX),
        )
        return local_root, tuple((target, target + cls.__PGET_STATUS_FILE_SUFFIX) for target in targets)

    @staticmethod
    def __pget_status_file_size(status_path: str) -> Optional[int]:
        try:
            with open(status_path, "rb") as handle:
                parsed_status = parse_lftp_pget_status_bytes(
                    handle.read(MAX_LFTP_PGET_STATUS_BYTES + 1)
                )
        except (OSError, UnicodeError):
            return None
        return parsed_status.total_size if parsed_status is not None else None

    @classmethod
    def __allow_legacy_get_resume(cls, local_dir: str, name: str, expected_size: int) -> bool:
        """Verify the physical constraints for a v0.9.2 contiguous upgrade.

        This is intentionally narrower than ordinary sidecarless get -c: a
        legacy timestamp cannot bind pget's non-contiguous segment map.  One
        contained regular target, no status map, and a bounded partial are the
        minimum physical evidence required before asking LFTP to continue it.
        """
        if type(expected_size) is not int or expected_size < 0:
            raise LftpError("Legacy resume requires a valid remote size")
        local_root = os.path.abspath(os.path.normpath(local_dir))
        local_name = os.path.join(*name.split("/"))
        targets: list[os.stat_result] = []
        for target_path in (
                os.path.join(local_root, local_name),
                os.path.join(local_root, local_name + cls.__LFTP_TEMP_FILE_SUFFIX),
        ):
            status_path = target_path + cls.__PGET_STATUS_FILE_SUFFIX
            if not cls.__is_lexically_and_really_contained(target_path, local_root) or \
                    not cls.__is_lexically_and_really_contained(status_path, local_root):
                raise LftpError("LFTP legacy resume target is outside the local directory")
            try:
                status_info = os.stat(status_path, follow_symlinks=False)
            except FileNotFoundError:
                status_info = None
            except OSError as error:
                raise LftpError("LFTP legacy resume status sidecar is unsafe") from error
            if status_info is not None:
                raise LftpError("Cannot migrate a legacy partial with an LFTP pget status map")
            try:
                target_info = os.stat(target_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise LftpError("LFTP legacy resume target is unsafe") from error
            if cls.__is_link_or_reparse(target_path, target_info) or not stat.S_ISREG(target_info.st_mode):
                raise LftpError("LFTP legacy resume target must be a regular file")
            targets.append(target_info)
        if len(targets) != 1:
            raise LftpError("Legacy resume requires exactly one local partial")
        if targets[0].st_size > expected_size:
            raise LftpError("Legacy resume partial exceeds the remote size")
        return True

    @staticmethod
    def __is_lexically_and_really_contained(path: str, root: str) -> bool:
        try:
            path_abs = os.path.abspath(os.path.normpath(path))
            root_abs = os.path.abspath(os.path.normpath(root))
            lexical_common = os.path.commonpath([path_abs, root_abs])
            real_common = os.path.commonpath([
                os.path.realpath(path_abs),
                os.path.realpath(root_abs),
            ])
        except (OSError, ValueError):
            return False
        return (
            os.path.normcase(lexical_common) == os.path.normcase(root_abs)
            and os.path.normcase(real_common) == os.path.normcase(os.path.realpath(root_abs))
        )

    @staticmethod
    def __is_link_or_reparse(path: str, file_info: Optional[os.stat_result] = None) -> bool:
        try:
            file_info = file_info or os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(file_info.st_mode):
            return True
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(getattr(file_info, "st_file_attributes", 0) & reparse_attribute)

    @staticmethod
    def __validate_queue_name(name: str) -> None:
        if (
            not isinstance(name, str)
            or not name
            or os.path.isabs(name)
            or os.path.splitdrive(name)[0]
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise LftpError("LFTP queue name must be a safe relative path")

    @staticmethod
    def __is_valid_pget_status_file(status_path: str) -> bool:
        """Recognize the segment-map shape emitted by LFTP's pget status.

        The status suffix alone is not enough to preserve a multi-connection
        resume: stale or malformed files must take the contiguous ``get -c``
        path instead.  This mirrors the repository scanner's bounded status
        parser without making queue construction depend on the scanner owner.
        """
        try:
            with open(status_path, "rb") as handle:
                return parse_lftp_pget_status_bytes(
                    handle.read(MAX_LFTP_PGET_STATUS_BYTES + 1)
                ) is not None
        except (OSError, UnicodeError):
            return False

    @classmethod
    def is_valid_pget_status_file(cls, status_path: str) -> bool:
        """Whether a PGET sidecar is a safe, resumable checkpoint.

        This accepts paired segment maps and conservative base-only maps.
        Neither form is completion proof; the sidecar remains resume metadata
        until a later physical scan observes completion without it.
        """
        return cls.__is_valid_pget_status_file(status_path)

    def queue(self,
              name: str,
              is_dir: bool,
              remote_base_dir_path: Optional[str] = None,
              local_base_dir_path: Optional[str] = None,
              exclude_patterns: str | Iterable[str | ExactPathExclusion] | None = None,
              allow_resume: bool = True,
              allow_legacy_get_resume: bool = False,
              expected_size: Optional[int] = None,
              trace_flow_id: Optional[str] = None):
        """
        Queues a job for download
        This method may cause an exception to be generated in a later method call:
          * Wrong type (is_dir) is specified
          * File/folder does not exist
        :param name: name of file or folder to download
        :param is_dir: true if folder, false if file
        :return:
        """
        self.__validate_queue_name(name)
        remote_dir = remote_base_dir_path if remote_base_dir_path is not None else self.__base_remote_dir_path
        local_dir = local_base_dir_path if local_base_dir_path is not None else self.__base_local_dir_path

        has_existing_target = False
        has_valid_pget_map = False
        if not is_dir:
            has_existing_target, has_valid_pget_map = self.__file_resume_artifacts(
                local_dir, name, expected_size if allow_resume else None,
                getattr(self, "_Lftp__breadcrumb_trace", None),
                trace_flow_id,
            )
        legacy_get_resume = False
        if allow_legacy_get_resume:
            if is_dir or allow_resume:
                raise LftpError("Legacy resume is only valid for a changed file without a source binding")
            # A timestamp alone is not a partial.  With an empty, already
            # validated artifact snapshot, start an ordinary fresh pget
            # instead of sending a legacy get-c that has nothing to continue.
            if has_existing_target:
                legacy_get_resume = self.__allow_legacy_get_resume(local_dir, name, expected_size)  # type: ignore[arg-type]
        if not is_dir and not allow_resume and has_existing_target:
            # LFTP pget without -c does not clobber a pre-existing target.
            # Do not queue a transfer that will predictably fail, and do not
            # delete or rename a user's staging artifact outside the existing
            # Delete Local lifecycle.  In particular, get -e could leave a
            # pget map beside a differently configured temporary target.
            if not legacy_get_resume:
                raise LftpError(
                    "Cannot safely restart a changed remote file while a local partial exists; "
                    "use Delete Local before Queue"
                )
        use_get = not is_dir and (legacy_get_resume or (has_existing_target and not has_valid_pget_map))
        parts = [
            "queue",
            "mirror" if is_dir else ("get" if use_get else "pget"),
        ]
        if is_dir or allow_resume or not has_existing_target or legacy_get_resume:
            parts.append("-c")
        if is_dir:
            user_exclude_patterns, exact_exclude_paths = partition_transfer_exclusions(exclude_patterns)
            if user_exclude_patterns:
                parts.extend(
                    "--exclude-glob {}".format(Lftp.__quote_command_argument(pattern))
                    for pattern in user_exclude_patterns
                )
            # LFTP's glob exclusions match basenames at every depth.  Its
            # regex exclusion is evaluated against the source-root-relative
            # path, so anchors make this an exact leaf exclusion.
            parts.extend(
                "--exclude {}".format(Lftp.__quote_command_argument(
                    "^{}$".format(re.escape(exact_path.relative_path))
                ))
                for exact_path in exact_exclude_paths
            )

        parts.append(Lftp.__quote_command_argument("{remote_dir}/{filename}".format(
            remote_dir=remote_dir,
            filename=name,
        )))
        if is_dir:
            parts.append(Lftp.__quote_command_argument("{local_dir}/".format(local_dir=local_dir)))
        else:
            local_destination = "{local_dir}/".format(local_dir=local_dir)
            if "/" in name:
                local_destination = os.path.join(local_dir, *name.split("/"))
                if not self.__is_lexically_and_really_contained(local_destination, local_dir):
                    raise LftpError("LFTP queue target is outside the local directory")
                try:
                    os.makedirs(os.path.dirname(local_destination), exist_ok=True)
                except OSError as error:
                    raise LftpError("LFTP queue target directory is unsafe") from error
            parts.extend([
                "-o",
                Lftp.__quote_command_argument(local_destination),
            ])
        command = " ".join(parts)
        command_metrics = None
        if _safe_lftp_queue_trace_flow(trace_flow_id) is not None and _breadcrumb_effectively_enabled(
                getattr(self, "_Lftp__breadcrumb_trace", None), LFTP_COMMAND_TRACE_CATEGORY, "debug",
        ):
            try:
                command_byte_length = len(command.encode("utf-8", "surrogateescape"))
            except UnicodeEncodeError:
                command_byte_length = -1
            command_metrics = (
                len(parts), len(user_exclude_patterns) + len(exact_exclude_paths) if is_dir else 0,
                command_byte_length,
            )
        self.logger.debug("queue command: %s", command)
        trace_kwargs: dict[str, object] = {
                "require_prompt_ready": False,
                "low_latency": True,
        }
        if _safe_lftp_queue_trace_flow(trace_flow_id) is not None:
            trace_kwargs.update({
                "trace_command_kind": "mirror" if is_dir else ("get" if use_get else "pget"),
                "trace_flow_id": trace_flow_id,
            })
            if command_metrics is not None:
                trace_kwargs["trace_command_metrics"] = command_metrics
        trace = getattr(self, "_Lftp__breadcrumb_trace", None)
        if _lftp_pty_trace_enabled(trace, "debug") or _lftp_pty_trace_enabled(trace, "warning"):
            try:
                trace_kwargs["trace_pty_correlation"] = "lftp-pty:" + secrets.token_hex(8)
            except Exception:
                # The Queue command must retain its original admission path
                # when optional breadcrumb setup is unavailable.
                pass
        self.__run_command(command, **trace_kwargs)

    def kill(self,
             name: str,
             path_pair_id: Optional[str] = None,
             remote_path: Optional[str] = None,
             local_path: Optional[str] = None) -> bool:
        """
        Kill a queued or running job
        :param name:
        :return: True if job of given name was found, False otherwise
        """
        def find_matching_jobs() -> tuple[List[LftpJobStatus], List[LftpJobStatus], bool]:
            statuses = self.status()
            if statuses is None:
                # Parser failures come back as None; treat them as an empty
                # snapshot so the retry loop can keep probing safely.
                statuses = []
            status_poll_healthy = self.last_status_poll_healthy
            matching_jobs: List[LftpJobStatus] = []
            for status in statuses:
                if status.name != name:
                    continue
                if remote_path is not None and not self.__path_is_within(status.remote_path, remote_path):
                    continue
                if local_path is not None and not self.__path_is_within(status.local_path, local_path):
                    continue
                if remote_path is None and local_path is None and path_pair_id is not None and status.path_pair_id != path_pair_id:
                    continue
                matching_jobs.append(status)
            if not matching_jobs:
                self.logger.debug(
                    "Kill poll for '%s' saw statuses: %s",
                    name,
                    [
                        {
                            "id": status.id,
                            "name": status.name,
                            "state": getattr(status.state, "name", status.state),
                            "remote_path": status.remote_path,
                            "local_path": status.local_path,
                            "path_pair_id": status.path_pair_id,
                        }
                        for status in statuses
                    ]
                )
            return statuses, matching_jobs, status_poll_healthy

        killed_any = False
        attempts = 0
        while attempts < MAX_KILL_MATCH_ATTEMPTS:
            statuses, matching_jobs, status_poll_healthy = find_matching_jobs()
            if not matching_jobs:
                if statuses:
                    break
                if not status_poll_healthy:
                    attempts += 1
                    time.sleep(0.05)
                    continue
                break
            attempts += 1
            # The snapshot can contain duplicate jobs for the same file.  Send
            # every matching id before returning, while avoiding synchronous
            # convergence polls.  The next controller refresh remains
            # responsible for publishing the authoritative post-stop state.
            for job_to_kill in matching_jobs:
                killed_any = True
                # Note: there's a chance that job ids change between when we called status
                #       and when we execute the kill command.
                if job_to_kill.state == LftpJobStatus.State.RUNNING:
                    self.logger.debug("Killing running job '{}'...".format(name))
                    self.__run_command(
                        "kill {}".format(job_to_kill.id),
                        require_prompt_ready=False,
                        low_latency=True,
                    )  # type: ignore[arg-type]
                elif job_to_kill.state == LftpJobStatus.State.QUEUED:
                    self.logger.debug("Killing queued job '{}'...".format(name))
                    self.__run_command(
                        "queue --delete {}".format(job_to_kill.id),
                        require_prompt_ready=False,
                        low_latency=True,
                    )  # type: ignore[arg-type]
                else:
                    raise NotImplementedError("Unsupported state {}".format(str(job_to_kill.state)))
            break

        if not killed_any:
            self.logger.debug("Kill failed to find job '{}'".format(name))
            return False
        return True

    def kill_all(self):
        """
        Kills are jobs, queued or downloading
        :return:
        """
        # empty the queue and kill running jobs
        self.__run_command("queue -d *", require_prompt_ready=False)  # type: ignore[arg-type]
        self.__run_command("kill all", require_prompt_ready=False, timeout_seconds=0)  # type: ignore[arg-type]

    def exit(self):
        """
        Exit the lftp instance. It cannot be used after being killed
        :return:
        """
        self.kill_all()
        self.__process.sendline("exit")
        self.__process.close(force=True)

    def force_close(self) -> None:
        """Interrupt a blocked PTY operation during controller teardown only."""
        try:
            self.__process.close(force=True)
        except (OSError, pexpect.exceptions.ExceptionPexpect):
            self.logger.debug("Lftp process was already closed during forced teardown")

    # Mark decorators as static (must be at end of class)
    # Source: https://stackoverflow.com/a/3422823
    with_check_process = staticmethod(with_check_process)  # type: ignore[arg-type]
