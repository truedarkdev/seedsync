# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
from abc import ABC, abstractmethod
from typing import Callable, Optional, Protocol, cast
import fnmatch
from threading import Lock
import os

from common import overrides, Constants, Context, Persist, PersistError, Serializable
from model import IModelListener, ModelFile
from .controller import Controller


CandidateMatch = tuple[ModelFile, Optional["AutoQueuePattern"]]


class _AutoQueueController(Protocol):
    def get_model_files(self) -> list[ModelFile]: ...
    def get_model_files_and_add_listener(self, listener: IModelListener) -> list[ModelFile]: ...
    def is_file_stopped(self, filename: str) -> bool: ...
    def clear_extracted_marker(self, file: ModelFile) -> None: ...
    def queue_command(self, command: Controller.Command) -> None: ...


def _config_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError("Auto-queue setting '{}' must be a boolean".format(name))
    return value


def _json_object(content: str) -> dict[str, object]:
    value = json.loads(content)
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object")
    return cast(dict[str, object], value)


class AutoQueuePattern(Serializable):
    # Keys
    __KEY_PATTERN = "pattern"

    def __init__(self, pattern: str):
        self.__pattern = pattern

    @property
    def pattern(self) -> str:
        return self.__pattern

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AutoQueuePattern):
            return False
        return self.__pattern == other.__pattern

    def __hash__(self) -> int:
        return hash(self.__pattern)

    def to_str(self) -> str:
        dct: dict[str, str] = {}
        dct[AutoQueuePattern.__KEY_PATTERN] = self.__pattern
        return json.dumps(dct)

    @classmethod
    def from_str(cls, content: str) -> "AutoQueuePattern":
        dct = _json_object(content)
        pattern = dct[AutoQueuePattern.__KEY_PATTERN]
        if not isinstance(pattern, str):
            raise TypeError("Auto-queue pattern must be a string")
        return AutoQueuePattern(pattern=pattern)


class IAutoQueuePersistListener(ABC):
    """Listener for receiving AutoQueuePersist events"""

    @abstractmethod
    def pattern_added(self, pattern: AutoQueuePattern) -> None:
        pass

    @abstractmethod
    def pattern_removed(self, pattern: AutoQueuePattern) -> None:
        pass


class AutoQueuePersist(Persist):
    """
    Persisting state for auto-queue
    """

    # Keys
    __KEY_PATTERNS = "patterns"

    def __init__(self):
        self.__patterns: list[AutoQueuePattern] = []
        self.__listeners: list[IAutoQueuePersistListener] = []
        self.__listeners_lock = Lock()

    @property
    def patterns(self) -> set[AutoQueuePattern]:
        return set(self.__patterns)

    def add_pattern(self, pattern: AutoQueuePattern) -> None:
        # Check values
        if not pattern.pattern.strip():
            raise ValueError("Cannot add blank pattern")

        if pattern not in self.__patterns:
            self.__patterns.append(pattern)
            with self.__listeners_lock:
                listeners = list(self.__listeners)
            for listener in listeners:
                listener.pattern_added(pattern)

    def remove_pattern(self, pattern: AutoQueuePattern) -> None:
        if pattern in self.__patterns:
            self.__patterns.remove(pattern)
            with self.__listeners_lock:
                listeners = list(self.__listeners)
            for listener in listeners:
                listener.pattern_removed(pattern)

    def add_listener(self, listener: IAutoQueuePersistListener) -> None:
        with self.__listeners_lock:
            self.__listeners.append(listener)

    @classmethod
    @overrides(Persist)
    def from_str(cls: type["AutoQueuePersist"], content: str) -> "AutoQueuePersist":
        persist = cls()
        try:
            dct = _json_object(content)
            pattern_list = dct[AutoQueuePersist.__KEY_PATTERNS]
            if not isinstance(pattern_list, list):
                raise TypeError("Auto-queue patterns must be a list")
            for pattern in cast(list[object], pattern_list):
                if not isinstance(pattern, str):
                    raise TypeError("Serialized auto-queue pattern must be a string")
                persist.add_pattern(AutoQueuePattern.from_str(pattern))
            return persist
        except (ValueError, TypeError, KeyError) as e:
            raise PersistError("Error parsing AutoQueuePersist - {}: {}".format(
                type(e).__name__, str(e))
            )

    @overrides(Persist)
    def to_str(self) -> str:
        dct: dict[str, list[str]] = {}
        dct[AutoQueuePersist.__KEY_PATTERNS] = list(p.to_str() for p in self.__patterns)
        return json.dumps(dct, indent=Constants.JSON_PRETTY_PRINT_INDENT)


class AutoQueueModelListener(IModelListener):
    """Keeps track of added and modified files"""
    def __init__(self):
        self.new_files: list[ModelFile] = []
        self.modified_files: list[tuple[ModelFile, ModelFile]] = []

    @overrides(IModelListener)
    def file_added(self, file: ModelFile) -> None:
        self.new_files.append(file)

    @overrides(IModelListener)
    def file_updated(self, old_file: ModelFile, new_file: ModelFile) -> None:
        self.modified_files.append((old_file, new_file))

    @overrides(IModelListener)
    def file_removed(self, file: ModelFile) -> None:
        pass


class AutoQueuePersistListener(IAutoQueuePersistListener):
    """Keeps track of newly added patterns"""
    def __init__(self):
        self.new_patterns: set[AutoQueuePattern] = set()
        self.__lock = Lock()

    @overrides(IAutoQueuePersistListener)
    def pattern_added(self, pattern: AutoQueuePattern) -> None:
        with self.__lock:
            self.new_patterns.add(pattern)

    @overrides(IAutoQueuePersistListener)
    def pattern_removed(self, pattern: AutoQueuePattern) -> None:
        with self.__lock:
            self.new_patterns.discard(pattern)

    def drain_new_patterns(self) -> set[AutoQueuePattern]:
        with self.__lock:
            drained = set(self.new_patterns)
            self.new_patterns.clear()
            return drained

    def restore_new_patterns(self, patterns: set[AutoQueuePattern]) -> None:
        with self.__lock:
            self.new_patterns.update(patterns)


class AutoQueue:
    """
    Implements auto-queue functionality by sending commands to controller
    as matching files are discovered
    AutoQueue is in the same thread as Controller, so no synchronization is
    needed for now
    """
    def __init__(self,
                 context: Context,
                 persist: AutoQueuePersist,
                 controller: _AutoQueueController):
        self.logger = context.logger.getChild("AutoQueue")
        self.__breadcrumb_trace = getattr(context, "breadcrumb_trace", None)
        self.__target_archive_trace_logger = self.logger.getChild("TargetArchiveTrace")
        self.__target_archive_trace_file_id = os.environ.get("SEEDSYNC_TARGET_ARCHIVE_TRACE_FILE_ID")
        if self.__target_archive_trace_file_id is not None and not self.__target_archive_trace_file_id.strip():
            self.__target_archive_trace_file_id = None
        self.__target_archive_trace_last_signature: Optional[str] = None
        self.__persist = persist
        self.__controller = controller
        self.__model_listener = AutoQueueModelListener()
        self.__persist_listener = AutoQueuePersistListener()
        self.__enabled = _config_bool(context.config.autoqueue.enabled, "enabled")
        self.__patterns_only = _config_bool(context.config.autoqueue.patterns_only, "patterns_only")
        self.__auto_extract_enabled = _config_bool(context.config.autoqueue.auto_extract, "auto_extract")
        self.__auto_delete_remote_enabled = _config_bool(
            context.config.autoqueue.auto_delete_remote, "auto_delete_remote"
        )
        self.__path_pair_manager = context.path_pair_manager
        self.__pair_auto_queue: dict[str, bool] = {}
        self.__queue_enabled = self.__enabled
        self.__cycle_sequence = 0

        self.__refresh_queue_state()

        # Register listeners whenever queueing might become active, including
        # when path pairs are present and own the queue decision.
        if self.__enabled or self.__queue_enabled or self.__auto_extract_enabled or self.__auto_delete_remote_enabled \
                or self.__path_pair_manager is not None:
            persist.add_listener(self.__persist_listener)

            initial_model_files = self.__controller.get_model_files_and_add_listener(self.__model_listener)
            # pass the initial model files through to our listener
            for file in initial_model_files:
                self.__model_listener.file_added(file)

            # Print the initial persist state
            self.logger.debug("Auto-Queue Patterns:")
            for pattern in self.__persist.patterns:
                self.logger.debug("    {}".format(pattern.pattern))

    @staticmethod
    def __extract_trace_selector_name(identifier: Optional[str]) -> Optional[str]:
        if identifier is None:
            return None
        try:
            parsed_identifier = json.loads(identifier)
        except (TypeError, ValueError, json.JSONDecodeError):
            return identifier
        if isinstance(parsed_identifier, list):
            parsed_items = cast(list[object], parsed_identifier)
            if len(parsed_items) == 2 and isinstance(parsed_items[1], str):
                return parsed_items[1]
        return identifier

    def __is_target_archive_trace_enabled(self) -> bool:
        return self.__target_archive_trace_file_id is not None

    def __target_archive_trace_selector_matches_file(self, file: ModelFile) -> bool:
        if not self.__is_target_archive_trace_enabled():
            return False
        if self.__target_archive_trace_file_id == file.file_id or self.__target_archive_trace_file_id == file.name:
            return True
        selector_name = self.__extract_trace_selector_name(self.__target_archive_trace_file_id)
        return selector_name == file.name

    def __trace_target_archive_event(self, event: str, payload: dict[str, object]) -> None:
        if not self.__is_target_archive_trace_enabled():
            return
        trace_payload: dict[str, object] = {
            "event": event,
            "target_selector": self.__target_archive_trace_file_id,
        }
        trace_payload.update(payload)
        signature = json.dumps(trace_payload, sort_keys=True)
        if signature == self.__target_archive_trace_last_signature:
            return
        self.__target_archive_trace_last_signature = signature
        self.__target_archive_trace_logger.info("target_archive_trace %s", signature)

    def process(self) -> None:
        """
        Advance the auto queue state
        :return:
        """
        self.__refresh_queue_state()
        if not self.__enabled and not self.__queue_enabled:
            self.__discard_inactive_buffers()
            return
        self.__cycle_sequence += 1
        new_patterns = self.__persist_listener.drain_new_patterns()

        try:
            ###
            # Queue
            ###
            queue_candidates: dict[str, ModelFile] = {}
            new_files_to_queue: list[CandidateMatch] = []
            modified_files_actual_update: list[CandidateMatch] = []
            modified_files_remote_discovery: list[CandidateMatch] = []
            files_to_queue: list[CandidateMatch] = []
            queue_blocked_reason_counts: dict[str, int] = {}
            queue_blocked_samples: list[dict[str, str]] = []
            if self.__queue_enabled:
                new_files_to_queue = self.__filter_candidates(
                    candidates=self.__model_listener.new_files,
                    new_patterns=new_patterns,
                    accept=lambda f: (
                        f.remote_has_transferable_content and
                        f.state == ModelFile.State.DEFAULT and
                        (f.local_size is None or f.local_size == 0) and
                        self._is_auto_queue_enabled_for_file(f)
                    )
                )
                queue_candidates.update({file.file_id: file for file in self.__model_listener.new_files})

                modified_candidates_actual_update: list[ModelFile] = []
                modified_candidates_remote_discovery: list[ModelFile] = []
                for old_file, new_file in self.__model_listener.modified_files:
                    remote_discovery_changed = (
                        old_file.remote_size != new_file.remote_size
                        or (
                            not old_file.remote_has_transferable_content
                            and new_file.remote_has_transferable_content
                        )
                        or (
                            not old_file.remote_present
                            and new_file.remote_present
                            and new_file.remote_has_transferable_content
                        )
                    )
                    if remote_discovery_changed:
                        if old_file.remote_size is not None:
                            modified_candidates_actual_update.append(new_file)
                        else:
                            modified_candidates_remote_discovery.append(new_file)
                queue_candidates.update({file.file_id: file for file in modified_candidates_actual_update})
                queue_candidates.update({file.file_id: file for file in modified_candidates_remote_discovery})

                modified_files_actual_update = self.__filter_candidates(
                    candidates=modified_candidates_actual_update,
                    new_patterns=new_patterns,
                    accept=lambda f: f.remote_has_transferable_content and
                    f.state == ModelFile.State.DEFAULT and
                    self._is_auto_queue_enabled_for_file(f)
                )

                modified_files_remote_discovery = self.__filter_candidates(
                    candidates=modified_candidates_remote_discovery,
                    new_patterns=new_patterns,
                    accept=lambda f: (
                        f.remote_has_transferable_content and
                        f.state == ModelFile.State.DEFAULT and
                        (f.local_size is None or f.local_size == 0) and
                        self._is_auto_queue_enabled_for_file(f)
                    )
                )

                files_to_queue_dict: dict[str, CandidateMatch] = {
                    file.file_id: (file, pattern) for file, pattern in new_files_to_queue
                }
                for file, pattern in modified_files_actual_update + modified_files_remote_discovery:
                    files_to_queue_dict[file.file_id] = (file, pattern)
                files_to_queue = [
                    (file, pattern)
                    for file, pattern in files_to_queue_dict.values()
                    if not self.__controller.is_file_stopped(file.file_id)
                ]
                files_to_queue_by_id = {file.file_id: file for file, _ in files_to_queue}
                queue_blocked_reason_counts, queue_blocked_samples = self.__summarize_auto_queue_decisions(
                    lane="queue",
                    candidate_files=list(queue_candidates.values()),
                    selected_by_id=files_to_queue_by_id,
                )

            ###
            # Extract
            ###
            files_to_extract: list[CandidateMatch] = []
            extract_candidate_files: list[ModelFile] = []

            if self.__enabled and self.__auto_extract_enabled:
                # Candidate all new files
                extract_candidate_files += self.__model_listener.new_files

                # Candidate modified files that just became DOWNLOADED
                # But not files that went EXTRACTING -> DOWNLOADED (failed extraction)
                for old_file, new_file in self.__model_listener.modified_files:
                    if old_file.state != ModelFile.State.DOWNLOADED and \
                            old_file.state != ModelFile.State.EXTRACTING and \
                            new_file.state == ModelFile.State.DOWNLOADED:
                        extract_candidate_files.append(new_file)

                files_to_extract = self.__filter_candidates(
                    candidates=extract_candidate_files,
                    new_patterns=new_patterns,
                    accept=lambda f:
                        f.state == ModelFile.State.DOWNLOADED and
                        f.local_size is not None and
                        f.local_size > 0 and
                        f.is_extractable
                )
            files_to_extract_by_id = {file.file_id: file for file, _ in files_to_extract}
            extract_blocked_reason_counts, extract_blocked_samples = self.__summarize_auto_queue_decisions(
                lane="extract",
                candidate_files=extract_candidate_files,
                selected_by_id=files_to_extract_by_id,
            )

            trace_target_file = None
            if self.__is_target_archive_trace_enabled():
                model_files = self.__controller.get_model_files()
                trace_target_file = next(
                    (file for file in model_files if self.__target_archive_trace_selector_matches_file(file)),
                    None
                )
                if trace_target_file is None:
                    self.__trace_target_archive_event("auto_extract_decision", {
                        "decision": "not_found",
                        "reason": "not_present_in_model",
                    })
                else:
                    trace_target_in_new_files = any(
                        file.file_id == trace_target_file.file_id for file in self.__model_listener.new_files
                    )
                    trace_target_in_modified_files = any(
                        new_file.file_id == trace_target_file.file_id
                        for _, new_file in self.__model_listener.modified_files
                    )
                    trace_target_in_candidates = trace_target_in_new_files or trace_target_in_modified_files
                    trace_target_pattern = next(
                        (
                            pattern.pattern if pattern is not None else None
                            for file, pattern in files_to_extract
                            if file.file_id == trace_target_file.file_id
                        ),
                        None
                    )
                    trace_target_selected_for_extract = any(
                        file.file_id == trace_target_file.file_id
                        for file, _ in files_to_extract
                    )
                    if not self.__auto_extract_enabled:
                        decision = "disabled"
                        reason = "auto_extract_disabled"
                    elif trace_target_selected_for_extract:
                        decision = "queued"
                        reason = "eligible"
                    elif not trace_target_in_candidates:
                        decision = "not_considered"
                        reason = "not_new_or_recently_downloaded"
                    elif trace_target_file.state != ModelFile.State.DOWNLOADED:
                        decision = "blocked"
                        reason = "state_not_downloaded"
                    elif trace_target_file.local_size is None:
                        decision = "blocked"
                        reason = "missing_local_size"
                    elif trace_target_file.local_size <= 0:
                        decision = "blocked"
                        reason = "empty_local_size"
                    elif not trace_target_file.is_extractable:
                        decision = "blocked"
                        reason = "not_extractable"
                    elif self.__patterns_only:
                        decision = "blocked"
                        reason = "pattern_no_match"
                    else:
                        decision = "blocked"
                        reason = "filtered_out"

                    self.__trace_target_archive_event("auto_extract_decision", {
                        "decision": decision,
                        "reason": reason,
                        "file": {
                            "file_id": trace_target_file.file_id,
                            "name": trace_target_file.name,
                            "path_pair_id": trace_target_file.path_pair_id,
                            "path_pair_name": trace_target_file.path_pair_name,
                            "state": getattr(trace_target_file.state, "name", trace_target_file.state),
                            "local_size": trace_target_file.local_size,
                            "remote_size": trace_target_file.remote_size,
                            "is_extractable": trace_target_file.is_extractable,
                        },
                        "observed_in_cycle": {
                            "new_files": trace_target_in_new_files,
                            "modified_files": trace_target_in_modified_files,
                            "candidate": trace_target_in_candidates,
                        },
                        "pattern": trace_target_pattern,
                        "patterns_only": self.__patterns_only,
                        "auto_extract_enabled": self.__auto_extract_enabled,
                    })

            if self.__enabled and self.__auto_extract_enabled and self.__patterns_only:
                matched_extract_file_ids = {file.file_id for file, _ in files_to_extract}
                blocked_extract_candidates = [
                    new_file
                    for old_file, new_file in self.__model_listener.modified_files
                    if old_file.state != ModelFile.State.DOWNLOADED and
                    old_file.state != ModelFile.State.EXTRACTING and
                    new_file.state == ModelFile.State.DOWNLOADED and
                    new_file.local_size is not None and
                    new_file.local_size > 0 and
                    new_file.is_extractable and
                    new_file.file_id not in matched_extract_file_ids
                ]
                for file in blocked_extract_candidates:
                    self.__controller.clear_extracted_marker(file)

            ###
            # Delete Remote
            ###
            files_to_delete_remote = self.__filter_delete_remote_candidates() if self.__enabled else []

            self.__record_breadcrumb(
                "auto_queue_cycle",
                {
                    "cycle": self.__cycle_sequence,
                    "new_queue_candidates": len(new_files_to_queue),
                    "modified_queue_candidates": len(modified_files_actual_update) + len(modified_files_remote_discovery),
                    "queue_count": len(files_to_queue),
                    "extract_count": len(files_to_extract),
                    "patterns_only": self.__patterns_only,
                    "auto_extract_enabled": self.__auto_extract_enabled,
                    "queue_blocked_reason_counts": queue_blocked_reason_counts,
                    "extract_blocked_reason_counts": extract_blocked_reason_counts,
                    "blocked_samples": (queue_blocked_samples + extract_blocked_samples)[:5],
                }
            )

            ###
            # Send commands
            ###

            # Send the queue commands
            for file, pattern in files_to_queue:
                self.logger.info(
                    "Auto queueing '{}'".format(file.name) +
                    (" for pattern '{}'".format(pattern.pattern) if pattern else "")
                )
                command = Controller.Command(
                    Controller.Command.Action.QUEUE,
                    file.file_id,
                    flow_id="autoq:{}:QUEUE:{}".format(self.__cycle_sequence, file.file_id),
                    origin="auto_queue",
                )
                self.__controller.queue_command(command)

            # Send the extract commands
            for file, pattern in files_to_extract:
                self.logger.info(
                    "Auto extracting '{}'".format(file.name) +
                    (" for pattern '{}'".format(pattern.pattern) if pattern else "")
                )
                command = Controller.Command(
                    Controller.Command.Action.EXTRACT,
                    file.file_id,
                    flow_id="autoq:{}:EXTRACT:{}".format(self.__cycle_sequence, file.file_id),
                    origin="auto_queue",
                )
                self.__controller.queue_command(command)

            # Send the delete remote commands (after extract so extraction happens first)
            for file, pattern in files_to_delete_remote:
                self.logger.info(
                    "Auto deleting remote '{}'".format(file.name) +
                    (" for pattern '{}'".format(pattern.pattern) if pattern else "")
                )
                command = Controller.Command(
                    Controller.Command.Action.DELETE_REMOTE,
                    file.file_id,
                    flow_id="autoq:{}:DELETE_REMOTE:{}".format(self.__cycle_sequence, file.file_id),
                    origin="auto_queue",
                )
                self.__controller.queue_command(command)

            # Clear the processed files
            self.__model_listener.new_files.clear()
            self.__model_listener.modified_files.clear()

        except Exception:
            current_patterns = self.__persist.patterns
            self.__persist_listener.restore_new_patterns({
                pattern for pattern in new_patterns if pattern in current_patterns
            })
            raise

    def __record_breadcrumb(self, message: str, details: dict[str, object]) -> None:
        if self.__breadcrumb_trace is None:
            return
        self.__breadcrumb_trace.record(
            "auto_queue",
            message,
            details,
            stage="auto_queue",
            event_type="state_transition",
            corr_id="auto_queue",
        )

    def __refresh_queue_state(self) -> None:
        self.__queue_enabled = self.__enabled
        self.__pair_auto_queue = {}
        if self.__path_pair_manager is None:
            return
        try:
            enabled_pairs = list(self.__path_pair_manager.get_enabled_pairs() or [])
        except Exception:
            return
        if not enabled_pairs:
            return
        self.__pair_auto_queue = {pair.id: pair.auto_queue for pair in enabled_pairs}
        self.__queue_enabled = any(self.__pair_auto_queue.values())

    def __discard_inactive_buffers(self) -> None:
        self.__model_listener.new_files.clear()
        self.__model_listener.modified_files.clear()
        self.__persist_listener.drain_new_patterns()

    def _is_auto_queue_enabled_for_file(self, file: ModelFile) -> bool:
        if self.__pair_auto_queue:
            if file.path_pair_id is None:
                return False
            return self.__pair_auto_queue.get(file.path_pair_id, False)
        return True

    def __filter_candidates(self,
                            candidates: list[ModelFile],
                            new_patterns: set[AutoQueuePattern],
                            accept: Callable[[ModelFile], bool]) -> list[CandidateMatch]:
        """
        Given a list of candidate files, filter out those that match the accept criteria
        Also takes into consideration new patterns that were added
        The accept criteria is applied to candidates AND all existing files in case of
        new patterns
        :param candidates:
        :param accept:
        :return: list of (filename, pattern) pairs
        """
        # Files accepted and matched, filename -> pattern map
        # Filename key prevents a file from being accepted twice
        files_matched: dict[str, CandidateMatch] = {}

        # Step 1: run candidates through all the patterns if they are enabled
        #         otherwise accept all files
        for file in candidates:
            if self.__patterns_only:
                for pattern in self.__persist.patterns:
                    if accept(file) and self.__match(pattern, file):
                        files_matched[file.file_id] = (file, pattern)
                        break
            elif accept(file):
                files_matched[file.file_id] = (file, None)

        # Step 2: run new pattern through all the files
        if new_patterns:
            model_files = self.__controller.get_model_files()
            for new_pattern in new_patterns:
                for file in model_files:
                    if accept(file) and self.__match(new_pattern, file):
                        files_matched[file.file_id] = (file, new_pattern)

        return list(files_matched.values())

    def __summarize_auto_queue_decisions(self,
                                         lane: str,
                                         candidate_files: list[ModelFile],
                                         selected_by_id: dict[str, ModelFile]
                                         ) -> tuple[dict[str, int], list[dict[str, str]]]:
        reason_counts: dict[str, int] = {}
        samples: list[dict[str, str]] = []
        seen_ids: set[str] = set()

        for file in candidate_files:
            if file.file_id in seen_ids:
                continue
            seen_ids.add(file.file_id)
            if file.file_id in selected_by_id:
                continue

            if lane == "queue":
                reason = self.__queue_block_reason(file)
            else:
                reason = self.__extract_block_reason(file)

            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if len(samples) < 3:
                samples.append({
                    "lane": lane,
                    "file_id": file.file_id,
                    "file_name": file.name,
                    "reason": reason,
                })

        return reason_counts, samples

    def __filter_delete_remote_candidates(self) -> list[CandidateMatch]:
        """
        Select remote-delete candidates from true completion transitions only.
        This intentionally avoids startup seeding and new-pattern backfill.
        """
        if not self.__auto_delete_remote_enabled:
            return []

        files_matched: dict[str, CandidateMatch] = {}
        for old_file, new_file in self.__model_listener.modified_files:
            if not self.__is_remote_delete_transition(old_file, new_file):
                continue
            if self.__patterns_only:
                for pattern in self.__persist.patterns:
                    if self.__match(pattern, new_file):
                        files_matched[new_file.file_id] = (new_file, pattern)
                        break
            else:
                files_matched[new_file.file_id] = (new_file, None)

        return list(files_matched.values())

    @staticmethod
    def __is_remote_delete_transition(old_file: ModelFile, new_file: ModelFile) -> bool:
        if not new_file.remote_has_transferable_content:
            return False
        if old_file.state == new_file.state:
            return False
        if new_file.state == ModelFile.State.EXTRACTED:
            return old_file.state == ModelFile.State.EXTRACTING
        if new_file.state == ModelFile.State.DOWNLOADED:
            if new_file.is_extractable:
                return False
            return old_file.state not in (
                ModelFile.State.DOWNLOADED,
                ModelFile.State.EXTRACTED,
                ModelFile.State.EXTRACTING,
            )
        return False

    def __queue_block_reason(self, file: ModelFile) -> str:
        if file.remote_size is None:
            return "missing_remote"
        if not file.remote_has_transferable_content:
            return "no_transferable_remote_content"
        if file.state != ModelFile.State.DEFAULT:
            return "state_not_default"
        if file.local_size is not None and file.local_size != 0:
            return "local_present"
        if self.__patterns_only and not any(self.__match(pattern, file) for pattern in self.__persist.patterns):
            return "pattern_no_match"
        if self.__controller.is_file_stopped(file.file_id):
            return "explicitly_stopped"
        return "filtered_out"

    def __extract_block_reason(self, file: ModelFile) -> str:
        if not self.__auto_extract_enabled:
            return "auto_extract_disabled"
        if file.state != ModelFile.State.DOWNLOADED:
            return "state_not_downloaded"
        if file.local_size is None:
            return "missing_local_size"
        if file.local_size <= 0:
            return "empty_local_size"
        if not file.is_extractable:
            return "not_extractable"
        if self.__patterns_only and not any(self.__match(pattern, file) for pattern in self.__persist.patterns):
            return "pattern_no_match"
        return "filtered_out"

    @staticmethod
    def __match(pattern: AutoQueuePattern, file: ModelFile) -> bool:
        """
        Returns true is file matches the pattern
        :param pattern:
        :param file:
        :return:
        """
        # make the search case insensitive
        pattern_str = pattern.pattern.lower()
        filename = file.name.lower()
        # 1. pattern match
        # 2. wildcard match
        return pattern_str in filename or \
            fnmatch.fnmatch(filename, pattern_str)
