# Copyright 2017, Inderpreet Singh, All rights reserved.

import os
import re
from collections import deque
from typing import Deque, List
import logging

from common import AppError
from common.redaction import redact_sensitive_text
from .job_status import LftpJobStatus


class LftpJobStatusParserError(AppError):
    pass


redact_credentials = redact_sensitive_text


class LftpJobStatusParser:
    """
    Parses the output of lftp's "jobs -v" command into a LftpJobStatus
    """
    __WRONG_TYPE_FAILURE_PREFIXES = (
        "get: Access failed: Wrong type",
        "pget: Access failed: Wrong type",
        "pget-chunk: Access failed: Wrong type",
        "mirror: Access failed: Wrong type",
    )

    # python doesn't support partial inline-modified flags, so we need
    # to capture all case-sensitive cases here
    __SIZE_UNITS_REGEX = ("b|B|"
                          "k|kb|kib|K|Kb|KB|KiB|Kib|"
                          "m|mb|mib|M|Mb|MB|MiB|Mib|"
                          "g|gb|gib|G|Gb|GB|GiB|Gib")
    __TIME_UNITS_REGEX = r"(?P<eta_d>\d*d)?(?P<eta_h>\d*h)?(?P<eta_m>\d*m)?(?P<eta_s>\d*s)?"

    __QUOTED_FILE_NAME_REGEX = r"`(?P<name>.*)'"
    # Directory queueing emits only these mirror options before the remote and
    # local positional paths. `jobs -v` may render an exclusion argument
    # without the quotes used by the queue command, so neither form may be
    # mistaken for a positional remote path.
    __MIRROR_KNOWN_OPTION_REGEX = (
        r'(?:-c|--exclude(?:-glob)?\s+(?:"(?:\\.|[^"\\])*"|(?:\\.|[^\s])+))'
    )

    __QUEUE_DONE_REGEX = r"^\[(?P<id>\d+)\]\sDone\s\(queue\s\(.+\)\)"
    __QUEUE_COMMAND_ECHO_REGEX = r"^queue\s+(?:mirror|pget|get)(?:\s|$)"
    __STATUS_COMMAND_ECHO_MARKER = "jobs -v"
    __STATUS_COMMAND_ECHO_BRACKETED_LINE_REGEX = re.compile(r"^\[\d+\]\s+jobs -v(?:\s+&)?$")
    __STATUS_COMMAND_ECHO_STRUCTURED_LINE_REGEX = re.compile(
        r"^(?:"
        r"\[\d+\]\s+(?:queue|pget|get|mirror|Done)\b|"
        r"(?:Now executing:|-)\s*\[\d+\]\s+(?:pget|get|mirror)\b|"
        r"\d+\.\s+(?:pget|get|mirror)\b|"
        r"(?:Queue is |Commands queued:|(?:sftp|ftp|ftps)://|"
        r"\\(?:mirror|chunk|transfer)\s+|`|Getting file list|cd\s|chmod\s|file:)"
        r")"
    )
    __STATUS_COMMAND_ECHO_TOKEN_REGEX = re.compile(r"(?<![\w/])jobs -v(?![\w/])")

    def __init__(self):
        self.logger = logging.getLogger("LftpJobStatusParser")

    def set_base_logger(self, base_logger: logging.Logger):
        self.logger = base_logger.getChild("LftpJobStatusParser")

    @staticmethod
    def _size_to_bytes(size: str) -> int:
        """
        Parse the size string and return number of bytes
        :param size:
        :return:
        """
        if size == "0":
            return 0
        m = re.compile(r"(?P<number>\d+\.?\d*)\s*(?P<units>{})?".format(LftpJobStatusParser.__SIZE_UNITS_REGEX))
        result = m.search(size)
        if not result:
            raise ValueError("String '{}' does not match the size pattern".format(size))
        number = float(result.group("number"))
        unit = (result.group("units") or "b")[0].lower()
        multipliers = {'b': 1, 'k': 1024, 'm': 1024*1024, 'g': 1024*1024*1024}
        if unit not in multipliers.keys():
            raise ValueError("Unrecognized unit {} in size string '{}'".format(unit, size))
        return int(number*multipliers[unit])

    @staticmethod
    def _eta_to_seconds(eta: str) -> int:
        """
        Parse the time string and return number of seconds
        :param eta:
        :return:
        """
        m = re.compile(LftpJobStatusParser.__TIME_UNITS_REGEX)
        result = m.search(eta)
        if not result:
            raise ValueError("String '{}' does not match the eta pattern".format(eta))
        # the [:-1] below remove the last character
        eta_d = int((result.group("eta_d") or '0d')[:-1])
        eta_h = int((result.group("eta_h") or '0h')[:-1])
        eta_m = int((result.group("eta_m") or '0m')[:-1])
        eta_s = int((result.group("eta_s") or '0s')[:-1])
        return eta_d*24*3600 + eta_h*3600 + eta_m*60 + eta_s

    @staticmethod
    def __set_record_provenance(
            status: LftpJobStatus, shape: str,
            transfer_state: LftpJobStatus.TransferState,
    ) -> None:
        """Retain only fixed parser form and field-presence provenance."""
        status.set_record_provenance(
            shape,
            bytes_present=shape == "got" and transfer_state.size_local is not None,
            percent_present=shape == "got" and transfer_state.percent_local is not None,
            speed_present=transfer_state.speed is not None,
            eta_present=transfer_state.eta is not None,
        )

    @staticmethod
    def __quoted_spans(line: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        index = 0
        while index < len(line):
            if line[index] == '"':
                end = index + 1
                escaped = False
                while end < len(line):
                    if escaped:
                        escaped = False
                    elif line[end] == "\\":
                        escaped = True
                    elif line[end] == '"':
                        spans.append((index, end + 1))
                        index = end
                        break
                    end += 1
            elif line[index] == "`":
                end = line.rfind("'")
                if end > index:
                    spans.append((index, end + 1))
                    index = end
            elif line[index] == "'" and (index == 0 or line[index - 1].isspace()):
                end = line.find("'", index + 1)
                if end > index:
                    spans.append((index, end + 1))
                    index = end
            index += 1
        return spans

    @classmethod
    def __has_status_command_echo(cls, line: str) -> bool:
        marker = cls.__STATUS_COMMAND_ECHO_MARKER
        if marker not in line or line == marker:
            return False

        if not cls.__STATUS_COMMAND_ECHO_STRUCTURED_LINE_REGEX.match(line):
            return False
        quoted_spans = cls.__quoted_spans(line)
        for occurrence in re.finditer(re.escape(marker), line):
            if any(start <= occurrence.start() < end for start, end in quoted_spans):
                continue
            if cls.__STATUS_COMMAND_ECHO_TOKEN_REGEX.match(line, occurrence.start()):
                return True
            if line.startswith(("\\mirror ", "\\chunk ", "\\transfer ", "`")):
                return True
        return False

    def parse(self, output: str) -> List[LftpJobStatus]:
        statuses: list[LftpJobStatus] = []
        lines = [s.strip() for s in output.splitlines()]
        lines = list(filter(None, lines))  # remove blank lines
        # lftp in a PTY can leak bracketed-paste toggle lines into the status output.
        lines = [
            line for line in lines
            if line not in {
                "\x1b[?2004h",
                "\x1b[?2004l",
            }
        ]
        if any(LftpJobStatusParser.__STATUS_COMMAND_ECHO_BRACKETED_LINE_REGEX.match(line) for line in lines):
            raise LftpJobStatusParserError(
                "Lftp status output contained a bracketed status command echo"
            )
        # A queue command echoed before the status command is still part of
        # the captured framing. Check it before slicing away the preamble so
        # it cannot become a healthy empty snapshot.
        jobs_marker_index = next((i for i, l in enumerate(lines) if l == "jobs -v"), None)
        preamble = lines if jobs_marker_index is None else lines[:jobs_marker_index]
        if any(re.match(LftpJobStatusParser.__QUEUE_COMMAND_ECHO_REGEX, line) for line in preamble):
            raise LftpJobStatusParserError(
                "Lftp status output contained a queue command echo before the snapshot"
            )
        start = 0 if jobs_marker_index is None else jobs_marker_index + 1
        lines = lines[start:]
        # A status poll can race with PTY echo from the preceding queue
        # command. That output is not a complete `jobs -v` snapshot, so it
        # must never be interpreted as an authoritative empty job list.
        if any(re.match(LftpJobStatusParser.__QUEUE_COMMAND_ECHO_REGEX, line) for line in lines):
            raise LftpJobStatusParserError(
                "Lftp status output contained a queue command echo instead of a complete snapshot"
            )
        if any(LftpJobStatusParser.__has_status_command_echo(line) for line in lines):
            raise LftpJobStatusParserError(
                "Lftp status command echo was interleaved with transfer progress"
            )
        # remove any remaining 'jobs -v' lines
        lines = list(filter(lambda s: s != "jobs -v", lines))
        # remove any remaining log line
        lines = filter(lambda s: not re.match(r"^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}.*\s->\s.*$", s), lines)
        lines = deque(lines)
        has_wrong_type_failure = any(self.__is_wrong_type_failure_line(line) for line in lines)
        try:
            statuses += self.__parse_queue(lines)
        except ValueError as e:
            self.logger.warning("LftpJobStateParser skipping bad queue output: {}".format(str(e)))
            self.logger.debug("Bad status output:\n{}".format(redact_credentials(output)))
            return statuses
        try:
            statuses += self.__parse_jobs(lines)
        except ValueError as e:
            self.logger.warning("LftpJobStateParser skipping bad job output: {}".format(str(e)))
            self.logger.debug("Bad status output:\n{}".format(redact_credentials(output)))
        if has_wrong_type_failure and statuses and not any(
            status.state == LftpJobStatus.State.RUNNING for status in statuses
        ):
            return []
        return statuses

    @staticmethod
    def __is_wrong_type_failure_line(line: str) -> bool:
        line = line.lstrip()
        return any(line.startswith(prefix) for prefix in LftpJobStatusParser.__WRONG_TYPE_FAILURE_PREFIXES)

    @staticmethod
    def __parse_jobs(lines: Deque[str]) -> List[LftpJobStatus]:
        jobs: list[LftpJobStatus] = []
        logger = logging.getLogger("LftpJobStatusParser")

        # Header patterns
        # pget header
        pget_header_pattern = (r"^\[(?P<id>\d+)\]\s+"
                               r"(?P<command>pget|get)\s+"
                               r"(?P<flags>.*?)\s+"
                               r"(?P<lq>['\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                               r"-o\s+"
                               r"(?P<rq>['\"]|)(?P<local>.+)(?P=rq)$")  # greedy on purpose
        pget_header_m = re.compile(pget_header_pattern)

        # mirror header (downloading)
        mirror_known_options_header_pattern = (r"^\[(?P<id>\d+)\]\s+"
                                               r"mirror\s+"
                                               r"(?P<flags>{option}(?:\s+{option})*)\s+"
                                               r"(?P<lq>['\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                               r"(?P<rq>['\"]|)(?P<local>.+)(?P=rq)\s+"  # greedy on purpose
                                               r"--\s+"
                                               r"(?P<szlocal>\d+\.?\d*\s?({sz})?)"  # size=0 has no units
                                               r"\/"
                                               r"(?P<szremote>\d+\.?\d*\s?({sz})?)\s+"  # size=0 has no units
                                               r"\((?P<pctlocal>\d+)%\)"
                                               r"(\s+(?P<speed>\d+\.?\d*\s?({sz}))\/s)?$")\
            .format(option=LftpJobStatusParser.__MIRROR_KNOWN_OPTION_REGEX,
                    sz=LftpJobStatusParser.__SIZE_UNITS_REGEX)
        mirror_known_options_header_m = re.compile(mirror_known_options_header_pattern)

        mirror_header_pattern = (r"^\[(?P<id>\d+)\]\s+"
                                 r"mirror\s+"
                                 r"(?P<flags>.*?)\s+"
                                 r"(?P<lq>['\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                 r"(?P<rq>['\"]|)(?P<local>.+)(?P=rq)\s+"  # greedy on purpose
                                 r"--\s+"
                                 r"(?P<szlocal>\d+\.?\d*\s?({sz})?)"  # size=0 has no units
                                 r"\/"
                                 r"(?P<szremote>\d+\.?\d*\s?({sz})?)\s+"  # size=0 has no units
                                 r"\((?P<pctlocal>\d+)%\)"
                                 r"(\s+(?P<speed>\d+\.?\d*\s?({sz}))\/s)?$")\
            .format(sz=LftpJobStatusParser.__SIZE_UNITS_REGEX)
        mirror_header_m = re.compile(mirror_header_pattern)

        # mirror header (connecting or receiving file list)
        mirror_known_options_fl_header_pattern = (r"^\[(?P<id>\d+)\]\s+"
                                                  r"mirror\s+"
                                                  r"(?P<flags>{option}(?:\s+{option})*)\s+"
                                                  r"(?P<lq>['\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                                  r"(?P<rq>['\"]|)(?P<local>.+)(?P=rq)$")\
            .format(option=LftpJobStatusParser.__MIRROR_KNOWN_OPTION_REGEX)
        mirror_known_options_fl_header_m = re.compile(mirror_known_options_fl_header_pattern)

        mirror_fl_header_pattern = (r"^\[(?P<id>\d+)\]\s+"
                                    r"mirror\s+"
                                    r"(?P<flags>.*?)\s+"
                                    r"(?P<lq>['\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                    r"(?P<rq>['\"]|)(?P<local>.+)(?P=rq)$")  # greedy on purpose
        mirror_fl_header_m = re.compile(mirror_fl_header_pattern)

        # Data patterns
        filename_pattern = r"\\transfer\s" + LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX
        filename_m = re.compile(filename_pattern)

        chunk_at_pattern = (r"^" + LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX + r"\s+"
                            r"at\s+"
                            r"\d+\s+"  # this is NOT the local size
                            r"(?:\(\d+%\)\s+)?"  # this is NOT the local percent
                            r"((?P<speed>\d+\.?\d*\s?({sz}))\/s\s+)?"
                            r"(eta:(?P<eta>{eta})\s+)?"
                            r"\s*\[(?P<desc>.*)\]$")\
            .format(sz=LftpJobStatusParser.__SIZE_UNITS_REGEX,
                    eta=LftpJobStatusParser.__TIME_UNITS_REGEX)
        chunk_at_m = re.compile(chunk_at_pattern)

        chunk_at2_pattern = (r"^" + LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX + r"\s+"
                             r"at\s+"
                             r"\d+\s+"  # this is NOT the local size
                             r"(?:\(\d+%\))")  # this is NOT the local percent
        chunk_at2_m = re.compile(chunk_at2_pattern)

        chunk_got_pattern = (r"^" + LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX + r",\s+"
                             r"got\s+"
                             r"(?P<szlocal>\d+)\s+"
                             r"of\s+"
                             r"(?P<szremote>\d+)\s+"
                             r"\((?P<pctlocal>\d+)%\)"
                             r"(\s+(?P<speed>\d+\.?\d*\s?({sz}))\/s)?"
                             r"(\seta:(?P<eta>{eta}))?")\
            .format(sz=LftpJobStatusParser.__SIZE_UNITS_REGEX,
                    eta=LftpJobStatusParser.__TIME_UNITS_REGEX)
        chunk_got_m = re.compile(chunk_got_pattern)

        chunk_header_pattern = r"^\\chunk\s+\d+(?:-\d+)?$"
        chunk_header_m = re.compile(chunk_header_pattern)

        chmod_header_pattern = (r"chmod\s"
                                r"(?P<name>.*)")
        chmod_header_m = re.compile(chmod_header_pattern)

        chmod_pattern = (LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX +
                         r"\s\[\]")
        chmod_pattern_m = re.compile(chmod_pattern)

        mirror_pattern = (r"\\mirror\s"
                          + LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX + r"\s+"
                          r"--\s+"
                          r"(?P<szlocal>\d+\.?\d*\s?({sz})?)"  # size=0 has no units
                          r"\/"
                          r"(?P<szremote>\d+\.?\d*\s?({sz})?)\s+"  # size=0 has no units
                          r"\((?P<pctlocal>\d+)%\)"
                          r"(\s+(?P<speed>\d+\.?\d*\s?({sz}))\/s)?$")\
            .format(sz=LftpJobStatusParser.__SIZE_UNITS_REGEX)
        mirror_m = re.compile(mirror_pattern)

        mirror_empty_pattern = (r"\\mirror\s"
                                + LftpJobStatusParser.__QUOTED_FILE_NAME_REGEX + r"\s*$")
        mirror_empty_m = re.compile(mirror_empty_pattern)

        queue_done_m = re.compile(LftpJobStatusParser.__QUEUE_DONE_REGEX)

        # Orphan progress lines lftp can emit outside a job context, e.g.:
        #   "3.0K/s eta:3m [Receiving data]"
        #   "10M/s eta:1h2m [Making data connection]"
        orphan_progress_pattern = (
            r"^(?:\d+\.?\d*\s?({sz}))\/s\s+"
            r"eta:({eta})\s+"
            r"\[.*\]$"
        ).format(
            sz=LftpJobStatusParser.__SIZE_UNITS_REGEX,
            eta=LftpJobStatusParser.__TIME_UNITS_REGEX,
        )
        orphan_progress_m = re.compile(orphan_progress_pattern)

        # Partial progress fragments from PTY line-wrap, e.g.:
        #   "/s eta:25m [Receiving data]"  (tail of "347.3K/s eta:25m ...")
        partial_progress_pattern = (
            r"^\/s\s+"
            r"eta:({eta})\s+"
            r"\[.*\]$"
        ).format(eta=LftpJobStatusParser.__TIME_UNITS_REGEX)
        partial_progress_m = re.compile(partial_progress_pattern)

        # Chunk line-wrap fragments from long filenames, e.g.:
        #   "tmos.7.1.DV.HDR.H.265-TheFarm.mkv' at 22283455338 (0%) 427.6K/s eta:28m [Receiving data]"
        chunk_wrap_pattern = (
            r"^(?:[^`\\].*)?'\s+at\s+\d+\s+"
            r"(?:\(\d+%\)\s+)?"
            r"(?:(?:\d+\.?\d*\s?({sz}))\/s\s+)?"
            r"(?:eta:({eta})\s+)?"
            r"\s*\[.*\]$"
        ).format(
            sz=LftpJobStatusParser.__SIZE_UNITS_REGEX,
            eta=LftpJobStatusParser.__TIME_UNITS_REGEX,
        )
        chunk_wrap_m = re.compile(chunk_wrap_pattern)

        prev_job = None
        while lines:
            line = lines.popleft()

            # First line must be a valid job header
            if not (
                prev_job is not None or
                pget_header_m.match(line) or
                mirror_known_options_header_m.match(line) or
                mirror_header_m.match(line) or
                mirror_known_options_fl_header_m.match(line) or
                mirror_fl_header_m.match(line)
            ):
                if orphan_progress_m.match(line) or partial_progress_m.match(line) or chunk_wrap_m.match(line):
                    logger.warning("Skipping orphan lftp progress line: '%s'", line)
                    continue
                raise ValueError("First line is not a matching header '{}'".format(line))

            # Search for pget header
            result = pget_header_m.search(line)
            if result:
                # Next line must be the sftp line
                if len(lines) < 1 or "sftp" not in lines[0]:
                    raise ValueError("Missing the 'sftp' line for pget header '{}'".format(line))
                lines.popleft()  # pop the 'sftp' line

                # Data line may not exist. Peek before consuming so a following
                # job header stays in the outer loop when no chunk data exists.
                result_at = None
                result_at2 = None
                result_got = None
                if lines and (
                        chunk_at_m.search(lines[0]) or
                        chunk_at2_m.search(lines[0]) or
                        chunk_got_m.search(lines[0])
                ):
                    line = lines.popleft()  # data line
                    result_at = chunk_at_m.search(line)
                    result_at2 = chunk_at2_m.search(line)
                    result_got = chunk_got_m.search(line)

                id_ = int(result.group("id"))
                name = os.path.basename(os.path.normpath(result.group("remote")))
                flags = result.group("flags")
                type_ = (LftpJobStatus.Type.GET
                         if result.group("command") == "get"
                         else LftpJobStatus.Type.PGET)
                status = LftpJobStatus(job_id=id_,
                                       job_type=type_,
                                       state=LftpJobStatus.State.RUNNING,
                                       name=name,
                                       flags=flags,
                                       remote_path=result.group("remote"),
                                       local_path=result.group("local"))
                record_shape = "none"
                if result_at:
                    if result.group("remote") != result_at.group("name"):
                        raise ValueError("Mismatch between pget names '{}' vs '{}'".format(
                            result.group("remote"), result_at.group("name")
                        ))
                    size_local = None
                    percent_local = None
                    speed = None
                    if result_at.group("speed"):
                        speed = LftpJobStatusParser._size_to_bytes(result_at.group("speed"))
                    eta = None
                    if result_at.group("eta"):
                        eta = LftpJobStatusParser._eta_to_seconds(result_at.group("eta"))
                    transfer_state = LftpJobStatus.TransferState(
                        size_local,
                        None,  # size remote
                        percent_local,
                        speed,
                        eta
                    )
                    record_shape = "at"
                elif result_at2:
                    if result.group("remote") != result_at2.group("name"):
                        raise ValueError("Mismatch between pget names '{}' vs '{}'".format(
                            result.group("remote"), result_at2.group("name")
                        ))
                    transfer_state = LftpJobStatus.TransferState(None, None, None, None, None)
                    record_shape = "at"
                elif result_got:
                    got_group_basename = os.path.basename(os.path.normpath(result_got.group("name")))
                    if got_group_basename != name:
                        raise ValueError("Mismatch: filename '{}' but chunk data for '{}'"
                                         .format(name, got_group_basename))
                    size_local = int(result_got.group("szlocal"))
                    size_remote = int(result_got.group("szremote"))
                    percent_local = int(result_got.group("pctlocal"))
                    speed = None
                    if result_got.group("speed"):
                        speed = LftpJobStatusParser._size_to_bytes(result_got.group("speed"))
                    eta = None
                    if result_got.group("eta"):
                        eta = LftpJobStatusParser._eta_to_seconds(result_got.group("eta"))
                    transfer_state = LftpJobStatus.TransferState(
                        size_local,
                        size_remote,
                        percent_local,
                        speed,
                        eta
                    )
                    record_shape = "got"
                else:
                    # No data line at all
                    transfer_state = LftpJobStatus.TransferState(None, None, None, None, None)

                LftpJobStatusParser.__set_record_provenance(status, record_shape, transfer_state)
                status.total_transfer_state = transfer_state
                jobs.append(status)
                prev_job = status
                continue

            # Search for mirror header
            result = mirror_known_options_header_m.search(line) or mirror_header_m.search(line)
            if result:
                id_ = int(result.group("id"))
                name = os.path.basename(os.path.normpath(result.group("remote")))
                flags = result.group("flags")
                type_ = LftpJobStatus.Type.MIRROR
                status = LftpJobStatus(job_id=id_,
                                       job_type=type_,
                                       state=LftpJobStatus.State.RUNNING,
                                       name=name,
                                       flags=flags,
                                       remote_path=result.group("remote"),
                                       local_path=result.group("local"))
                size_local = LftpJobStatusParser._size_to_bytes(result.group("szlocal"))
                size_remote = LftpJobStatusParser._size_to_bytes(result.group("szremote"))
                percent_local = int(result.group("pctlocal"))
                speed = None
                if result.group("speed"):
                    speed = LftpJobStatusParser._size_to_bytes(result.group("speed"))
                transfer_state = LftpJobStatus.TransferState(
                    size_local,
                    size_remote,
                    percent_local,
                    speed,
                    None  # eta
                )
                LftpJobStatusParser.__set_record_provenance(status, "got", transfer_state)
                status.total_transfer_state = transfer_state
                jobs.append(status)
                prev_job = status
                # Continue the outer loop
                continue

            # Search for mirror connecting header
            # Note: this must be after the more restrictive mirror header above
            result = mirror_known_options_fl_header_m.search(line) or mirror_fl_header_m.search(line)
            if result:
                # There may be a 'Connecting' or 'cd' line ahead, but not always
                if lines and (
                        lines[0].startswith("Getting file list") or
                        lines[0].startswith("cd ")
                ):
                    lines.popleft()  # pop the connecting line
                id_ = int(result.group("id"))
                name = os.path.basename(os.path.normpath(result.group("remote")))
                flags = result.group("flags")
                type_ = LftpJobStatus.Type.MIRROR
                status = LftpJobStatus(job_id=id_,
                                       job_type=type_,
                                       state=LftpJobStatus.State.RUNNING,
                                       name=name,
                                       flags=flags,
                                       remote_path=result.group("remote"),
                                       local_path=result.group("local"))
                LftpJobStatusParser.__set_record_provenance(
                    status, "none", LftpJobStatus.TransferState(None, None, None, None, None),
                )
                jobs.append(status)
                prev_job = status
                # Continue the outer loop
                continue

            # Search for filename
            result = filename_m.search(line)
            if result:
                name = result.group("name")
                # Peek before consuming so a following job header stays in the
                # outer loop when this transfer emitted no chunk data.
                if not lines or not lines[0].startswith("`"):
                    continue
                line = lines.popleft()
                result_at = chunk_at_m.search(line)
                result_at2 = chunk_at2_m.search(line)
                result_got = chunk_got_m.search(line)
                assert prev_job is not None
                if result_at:
                    # filename is full path, but chunk name is only normpath
                    if result_at.group("name") != os.path.basename(os.path.normpath(name)):
                        raise ValueError("Mismatch: filename '{}' but chunk data for '{}'"
                                         .format(name, result_at.group("name")))
                    size_local = None
                    percent_local = None
                    speed = None
                    if result_at.group("speed"):
                        speed = LftpJobStatusParser._size_to_bytes(result_at.group("speed"))
                    eta = None
                    if result_at.group("eta"):
                        eta = LftpJobStatusParser._eta_to_seconds(result_at.group("eta"))
                    file_status = LftpJobStatus.TransferState(
                        size_local,
                        None,
                        percent_local,
                        speed,
                        eta
                    )
                    prev_job.add_active_file_transfer_state(name, file_status)
                elif result_at2:
                    # filename is full path, but chunk name is only normpath
                    if result_at2.group("name") != os.path.basename(os.path.normpath(name)):
                        raise ValueError("Mismatch: filename '{}' but chunk data for '{}'"
                                         .format(name, result_at2.group("name")))
                    file_status = LftpJobStatus.TransferState(None, None, None, None, None)
                    prev_job.add_active_file_transfer_state(name, file_status)
                elif result_got:
                    if result_got.group("name") != os.path.basename(os.path.normpath(name)):
                        raise ValueError("Mismatch: filename '{}' but chunk data for '{}'"
                                         .format(name, result_got.group("name")))
                    size_local = int(result_got.group("szlocal"))
                    size_remote = int(result_got.group("szremote"))
                    percent_local = int(result_got.group("pctlocal"))
                    speed = None
                    if result_got.group("speed"):
                        speed = LftpJobStatusParser._size_to_bytes(result_got.group("speed"))
                    eta = None
                    if result_got.group("eta"):
                        eta = LftpJobStatusParser._eta_to_seconds(result_got.group("eta"))
                    file_status = LftpJobStatus.TransferState(
                        size_local,
                        size_remote,
                        percent_local,
                        speed,
                        eta
                    )
                    prev_job.add_active_file_transfer_state(name, file_status)
                else:
                    raise ValueError("Missing chunk data for filename '{}'".format(name))
                # Continue the outer loop
                continue

            # Search for but ignore "\mirror" line
            result = mirror_m.search(line)
            if result:
                # Continue the outer loop
                continue
            result = mirror_empty_m.search(line)
            if result:
                name = result.group("name")
                # One of these lines may follow, ignore it as well
                #    "Getting files list"
                #    "cd"
                #    "<name>: "
                #    "mkdir"
                if lines:
                    if "Getting file list" in lines[0] or \
                            lines[0].startswith("cd ") or \
                            lines[0] == "{}:".format(name) or \
                            lines[0].startswith("mkdir "):
                        lines.popleft()
                # Continue the outer loop
                continue

            if prev_job is not None and (
                    line.startswith("Getting file list") or
                    line.startswith("cd ")
            ):
                continue

            # Search for but ignore "\chunk" line
            result = chunk_header_m.search(line)
            if result:
                # Also ignore the following chunk data line when lftp emits one.
                if lines and lines[0].startswith("`"):
                    lines.popleft()
                # Continue the outer loop
                continue

            # Search for but ignore "chmod" line
            result = chmod_header_m.search(line)
            if result:
                name = result.group("name")
                # Also ignore the next one or two lines
                if not lines or not lines[0].startswith("file:"):
                    raise ValueError("Missing 'file:' line for chmod '{}'".format(name))
                lines.popleft()
                if lines:
                    result_chmod = chmod_pattern_m.search(lines[0])
                    if result_chmod:
                        name_chmod = result_chmod.group("name")
                        if name != name_chmod:
                            raise ValueError("Mismatch in names chmod '{}'".format(name))
                        lines.popleft()
                # Continue the outer loop
                continue

            # Search for the Done line, but it better be the last line
            result = queue_done_m.match(line)
            if result:
                if lines:
                    raise ValueError("There are more lines after the 'Done' line")
                # Continue the outer loop
                continue

            if prev_job is not None:
                logger.warning("Skipping unrecognized line inside job context: '%s'", line)
                continue

            if orphan_progress_m.match(line) or partial_progress_m.match(line) or chunk_wrap_m.match(line):
                logger.warning("Skipping orphan lftp progress line: '%s'", line)
                continue

            # If we got here, then we don't know how to parse this line
            raise ValueError("Unable to parse line '{}'".format(line))
        return jobs

    @staticmethod
    def __parse_queue(lines: Deque[str]) -> List[LftpJobStatus]:
        queue: list[LftpJobStatus] = []

        queue_done_m = re.compile(LftpJobStatusParser.__QUEUE_DONE_REGEX)
        if len(lines) == 1:
            if not queue_done_m.match(lines[0]):
                raise ValueError("Unrecognized line '{}'".format(lines[0]))
            lines.popleft()

        if lines:
            # Look for the header lines
            if len(lines) < 2:
                raise ValueError("Missing queue header")
            header1_pattern = r"^\[\d+\] queue \((?:sftp|ftp|ftps)://.*@.*\)(?:\s+--\s+(?:\d+\.\d+|\d+)\s(?:{})\/s)?$"\
                              .format(LftpJobStatusParser.__SIZE_UNITS_REGEX)
            header2_pattern = "^(?:sftp|ftp|ftps)://.*@.*$"
            line = lines.popleft()
            if not re.match(header1_pattern, line):
                raise ValueError("Missing queue header line 1: {}".format(line))
            line = lines.popleft()
            if not re.match(header2_pattern, line):
                raise ValueError("Missing queue header line 2: {}".format(line))
            if not lines:
                raise ValueError("Missing queue status")

            # Look for 'Now executing' lines
            line = lines.popleft()
            if re.match("Queue is stopped.", line):
                # Nothing to do
                pass
            elif re.match("Now executing:", line):
                # Remove any more lines associated with 'now executing'
                while lines and re.match(r"^-\[\d+\]", lines[0]):
                    lines.popleft()

            # Look for the actual queue
            if lines and re.match("Commands queued:", lines[0]):
                lines.popleft()
                if not lines:
                    raise ValueError("Missing queued commands")

                # Parse the queued commands
                queue_pget_pattern = (r"^(?P<id>\d+)\.\s+"
                                      r"(?P<command>pget|get)\s+"
                                      r"(?P<flags>.*?)\s+"
                                      r"(?P<lq>[\'\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                      r"(?:-o\s+)"
                                      r"(?P<rq>[\'\"]|)(?P<local>.+)(?P=rq)$")  # greedy on purpose
                queue_pget_m = re.compile(queue_pget_pattern)
                queue_mirror_pattern = (r"^(?P<id>\d+)\.\s+"
                                        r"mirror\s+"
                                        r"(?P<flags>.*?)\s+"
                                        r"(?P<lq>[\'\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                        r"(?P<rq>[\'\"]|)(?P<local>.+)(?P=rq)$")  # greedy on purpose
                queue_mirror_m = re.compile(queue_mirror_pattern)
                queue_mirror_known_options_pattern = (r"^(?P<id>\d+)\.\s+"
                                                      r"mirror\s+"
                                                      r"(?P<flags>{option}(?:\s+{option})*)\s+"
                                                      r"(?P<lq>[\'\"]|)(?P<remote>.+)(?P=lq)\s+"  # greedy on purpose
                                                      r"(?P<rq>[\'\"]|)(?P<local>.+)(?P=rq)$")\
                    .format(option=LftpJobStatusParser.__MIRROR_KNOWN_OPTION_REGEX)
                queue_mirror_known_options_m = re.compile(queue_mirror_known_options_pattern)
                while lines:
                    line = lines[0]
                    if re.match(r"^\d+\.", line):
                        # header line
                        lines.popleft()

                        if "jobs -v" in line:
                            logging.getLogger("LftpJobStatusParser").warning(
                                "Failed to parse queue line, skipping: {}".format(line)
                            )
                            while lines and not (
                                re.match(r"^\d+\.", lines[0]) or
                                re.match(r"^cd\s.*$", lines[0]) or
                                re.match(r"^\[\d+\]", lines[0]) or
                                queue_done_m.match(lines[0])
                            ):
                                lines.popleft()
                            continue

                        result_pget = queue_pget_m.match(line)
                        result_mirror = (
                            queue_mirror_known_options_m.match(line) or
                            queue_mirror_m.match(line)
                        )
                        if result_pget:
                            type_ = (LftpJobStatus.Type.GET
                                     if result_pget.group("command") == "get"
                                     else LftpJobStatus.Type.PGET)
                            result = result_pget
                        elif result_mirror:
                            type_ = LftpJobStatus.Type.MIRROR
                            result = result_mirror
                        else:
                            logging.getLogger("LftpJobStatusParser").warning(
                                "Failed to parse queue line, skipping: {}".format(line)
                            )
                            while lines and not (
                                re.match(r"^\d+\.", lines[0]) or
                                re.match(r"^cd\s.*$", lines[0]) or
                                re.match(r"^\[\d+\]", lines[0]) or
                                queue_done_m.match(lines[0])
                            ):
                                lines.popleft()
                            continue
                        id_ = int(result.group("id"))
                        name = os.path.basename(os.path.normpath(result.group("remote")))
                        flags = result.group("flags")
                        status = LftpJobStatus(job_id=id_,
                                               job_type=type_,
                                               state=LftpJobStatus.State.QUEUED,
                                               name=name,
                                               flags=flags,
                                               remote_path=result.group("remote"),
                                               local_path=result.group("local"))
                        queue.append(status)
                    elif re.match(r"^cd\s.*$", line):
                        # 'cd' line after pget, ignore
                        lines.popleft()
                    else:
                        # no match, exit loop
                        break

            # Look for the done line
            if lines and queue_done_m.match(lines[0]):
                lines.popleft()

        return queue
