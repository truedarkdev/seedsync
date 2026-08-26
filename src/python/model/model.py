# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import copy
from abc import ABC, abstractmethod
from bisect import insort
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Iterator, Optional, Set
from threading import Lock

# my libs
from common import AppError
from .file import ModelFile


class ModelError(AppError):
    """
    Exception indicating a model error
    """
    pass


@dataclass(frozen=True)
class ActiveProgressOverlay:
    """Immutable, live-only presentation facts for one canonical root."""
    download_progress: Optional[int]
    transferred_size: Optional[int]
    downloading_speed: Optional[int]
    eta: Optional[int]


class IModelListener(ABC):
    """
    Interface to listen to model events
    """
    @abstractmethod
    def file_added(self, file: ModelFile) -> None:
        """
        Event indicating a file was added to the model
        :param file:
        :return:
        """
        pass

    @abstractmethod
    def file_removed(self, file: ModelFile) -> None:
        """
        Event indicating that the given file was removed from the model
        :param file:
        :return:
        """
        pass

    @abstractmethod
    def file_updated(self, old_file: ModelFile, new_file: ModelFile) -> None:
        """
        Event indicating that the given file was updated
        :param old_file:
        :param new_file:
        :return:
        """
        pass


class Model:
    """
    Represents the entire state of lftp
    """
    def __init__(self):
        self.logger = logging.getLogger("Model")
        self.__files_by_id: Dict[str, ModelFile] = {}
        self.__ordered_file_ids: list[str] = []
        self.__file_ids_by_name: Dict[str, Set[str]] = {}
        self.__listeners: list[IModelListener] = []
        self.__listeners_lock = Lock()
        # This is deliberately owned by the mutation boundary, rather than by
        # an individual renderer.  Consumers can use it to reject a page whose
        # cursor was produced from an older model without retaining a tree.
        self.__version = 0
        self.__scope_versions: Dict[Optional[str], int] = {}
        self.__tree_file_count = 0
        # Scoped publication may reuse roots only if their render overlay
        # matches the replacement roots' persisted timestamp snapshot.
        self.__downloaded_timestamp_overlay_generation = 0
        self.__version_publication_callback: Optional[Callable[[int, int], None]] = None
        # Authoritative scans own ModelFile topology and lifecycle state.  This
        # copy-on-write map owns only fresh LFTP presentation values.
        self.__active_progress_overlays: Dict[str, ActiveProgressOverlay] = {}
        # Kept with the live-only values so a later status can never be
        # mistaken for a continuation of a cleared LFTP job.
        self.__active_progress_overlay_job_identities: Dict[str, tuple[int, str]] = {}
        self.__suppress_active_progress_clear = 0
        self.__legacy_overlay_correction_file_ids: Set[str] = set()
        self.__legacy_overlay_correction_old_files: Dict[str, ModelFile] = {}
        # Transport must not combine a queued base ModelFile with whichever
        # live overlay happens to exist when an SSE writer eventually runs.
        # Keep the effective root record materialized at the same mutation
        # boundary as its version notification.  These snapshots are runtime
        # publication state only; scans remain authoritative for ModelFiles.
        self.__published_files_by_id: Dict[str, ModelFile] = {}

    def __refresh_published_file(self, file: ModelFile) -> ModelFile:
        """Materialize one immutable-effective root for asynchronous readers."""
        # Progress pulses can be frequent and roots may have very large trees.
        # Publication is intentionally shallow for the scoped hot path.
        # Legacy callbacks use a separate full-tree freeze only for lifecycle
        # events, never for per-pulse overlay publication.
        published = ModelFile(file.name, file.is_dir)
        published.state = file.state
        published.remote_size = file.remote_size
        published.local_size = file.local_size
        published.remote_present = file.remote_present
        published.local_present = file.local_present
        published.remote_has_transferable_content = file.remote_has_transferable_content
        published.transferred_size = file.transferred_size
        published.display_size_total = file.display_size_total
        published.display_transferred_size = file.display_transferred_size
        published.download_progress = file.download_progress
        published.downloading_speed = file.downloading_speed
        published.eta = file.eta
        published.is_extractable = file.is_extractable
        published.is_stoppable = file.is_stoppable
        published.explicitly_stopped = file.explicitly_stopped
        published.complete_local_coverage = file.complete_local_coverage
        published.resume_checkpoint_present = file.resume_checkpoint_present
        if file.local_created_timestamp is not None:
            published.local_created_timestamp = file.local_created_timestamp
        if file.local_modified_timestamp is not None:
            published.local_modified_timestamp = file.local_modified_timestamp
        if file.remote_created_timestamp is not None:
            published.remote_created_timestamp = file.remote_created_timestamp
        if file.remote_modified_timestamp is not None:
            published.remote_modified_timestamp = file.remote_modified_timestamp
        published.downloaded_timestamp = file.downloaded_timestamp
        published.validation_progress = file.validation_progress
        published.validation_error = file.validation_error
        published.corrupt_chunks = file.corrupt_chunks
        published.final_move_succeeded = file.final_move_succeeded
        published.path_pair_id = file.path_pair_id
        published.path_pair_name = file.path_pair_name
        overlay = self.__active_progress_overlays.get(file.file_id)
        if overlay is not None:
            published.download_progress = overlay.download_progress
            published.transferred_size = overlay.transferred_size
            published.downloading_speed = overlay.downloading_speed
            published.eta = overlay.eta
        self.__published_files_by_id[file.file_id] = published
        return published

    def published_file(self, file_id: str) -> Optional[ModelFile]:
        """Return the model-owned effective root published at its last revision.

        Callers must hold the controller model lock.  The returned object is a
        private immutable-by-convention transport snapshot, never a live root.
        """
        return self.__published_files_by_id.get(file_id)

    def __full_effective_file(self, file: ModelFile) -> ModelFile:
        """Freeze a recursive legacy callback record outside the hot path."""
        published = copy.deepcopy(file)
        overlay = self.__active_progress_overlays.get(file.file_id)
        if overlay is not None:
            published.download_progress = overlay.download_progress
            published.transferred_size = overlay.transferred_size
            published.downloading_speed = overlay.downloading_speed
            published.eta = overlay.eta
        return published

    @property
    def version(self) -> int:
        return self.__version

    @property
    def file_count(self) -> int:
        """O(1) root-file cardinality for local diagnostics."""
        return len(self.__files_by_id)

    @property
    def tree_file_count(self) -> int:
        return self.__tree_file_count

    @property
    def downloaded_timestamp_overlay_generation(self) -> int:
        return self.__downloaded_timestamp_overlay_generation

    def set_downloaded_timestamp_overlay_generation(self, value: int) -> None:
        self.__downloaded_timestamp_overlay_generation = value if type(value) is int and value >= 0 else 0

    def set_tree_file_count(self, value: int) -> None:
        self.__tree_file_count = value if type(value) is int and value >= 0 else 0

    @property
    def listener_count(self) -> int:
        """O(1) current listener cardinality without exposing listeners."""
        with self.__listeners_lock:
            return len(self.__listeners)

    def scope_version(self, path_pair_id: Optional[str]) -> int:
        """Version for one path-pair; unrelated pair mutations do not advance it."""
        return self.__scope_versions.get(path_pair_id, 0)

    def iter_files(self) -> Iterator[ModelFile]:
        """Read-only live root iterator; callers hold the controller model lock."""
        return iter(self.__files_by_id.values())

    def iter_files_by_id(self) -> Iterator[ModelFile]:
        """Stable canonical root order without allocating/sorting a snapshot."""
        return (self.__files_by_id[file_id] for file_id in self.__ordered_file_ids)

    @classmethod
    def compose_candidate(
            cls, live_model: "Model", removed_file_ids: Set[str], replacement_files: Iterable[ModelFile],
            tree_file_count: int, downloaded_timestamp_overlay_generation: Optional[int] = None,
    ) -> "Model":
        """Build an unobserved candidate reusing untouched live root objects.

        The updater holds its model lock while composing this short-lived
        transaction input.  Deliberately bypassing ``add_file`` preserves the
        live model's listeners and versions until the normal diff/lifecycle
        path decides which selected roots to publish.
        """
        if downloaded_timestamp_overlay_generation is not None and \
                live_model.downloaded_timestamp_overlay_generation != downloaded_timestamp_overlay_generation:
            raise ModelError("Cannot reuse roots rendered with a stale downloaded timestamp overlay")
        candidate = cls()
        candidate.logger = live_model.logger
        candidate.set_downloaded_timestamp_overlay_generation(
            live_model.downloaded_timestamp_overlay_generation
            if downloaded_timestamp_overlay_generation is None else downloaded_timestamp_overlay_generation
        )
        for file in live_model.iter_files():
            if file.file_id not in removed_file_ids:
                candidate.__insert_candidate_file(file)
        for file in replacement_files:
            candidate.__insert_candidate_file(file)
        candidate.set_tree_file_count(tree_file_count)
        return candidate

    def __insert_candidate_file(self, file: ModelFile) -> None:
        """Insert a root reference without notifications/version mutation."""
        file_id = file.file_id
        if file_id in self.__files_by_id:
            raise ModelError("Duplicate root while composing model candidate")
        self.__files_by_id[file_id] = file
        insort(self.__ordered_file_ids, file_id)
        self.__file_ids_by_name.setdefault(file.name, set()).add(file_id)

    def __notify_versioned_change(
            self, file: ModelFile, global_version: int, scope_version: int,
    ) -> None:
        """Notify optional lightweight listeners after a model mutation.

        The historic IModelListener contract transports ModelFile instances.
        New scoped web listeners intentionally consume only this primitive
        identity/version notification so a slow browser cannot retain models.
        """
        scope_id = file.path_pair_id
        file_id = file.file_id
        with self.__listeners_lock:
            listeners = list(self.__listeners)
        for listener in listeners:
            callback = getattr(listener, "model_version_changed", None)
            if callable(callback):
                callback(scope_version, scope_id, file_id)
            # Optional additive atomic callback. Scoped consumers must receive
            # both captured versions before publishing event availability.
            publication_callback = getattr(listener, "model_version_published", None)
            if callable(publication_callback):
                publication_callback(scope_version, global_version, scope_id, file_id)

    def __notify_legacy_publication_change(
            self, old_file: Optional[ModelFile], new_file: ModelFile,
    ) -> None:
        """Send legacy consumers the same committed projection as scoped ones."""
        if old_file is None:
            return
        with self.__listeners_lock:
            listeners = list(self.__listeners)
        for listener in listeners:
            listener.file_updated(old_file, new_file)

    def __notify_legacy_overlay_correction_if_needed(
            self, file_id: str, file: Optional[ModelFile],
    ) -> None:
        """Correct only a queued retained-overlay lifecycle replacement."""
        if file_id not in self.__legacy_overlay_correction_file_ids:
            return
        # A later healthy status can replace the live overlay before the
        # replacement lifecycle settles.  That is still active progress, not
        # the terminal/base correction the queued legacy record requires.
        if file_id in self.__active_progress_overlays:
            return
        self.__legacy_overlay_correction_file_ids.discard(file_id)
        old_file = self.__legacy_overlay_correction_old_files.pop(file_id, None)
        if file is None:
            return
        self.__notify_legacy_publication_change(
            old_file, self.__full_effective_file(file),
        )

    def notify_summary_changed(self) -> None:
        """Wake compact-summary listeners without inventing a file mutation.

        Scan inventory freshness is derived alongside the model source graph,
        but an empty or failed scan may not change a visible root.  Summary
        listeners still need one bounded wake-up for that state transition.
        """
        with self.__listeners_lock:
            listeners = list(self.__listeners)
        for listener in listeners:
            callback = getattr(listener, "model_summary_changed", None)
            if callable(callback):
                callback()

    def __advance_version(self, file: ModelFile) -> tuple[int, int]:
        self.__version += 1
        scope_id = file.path_pair_id
        self.__scope_versions[scope_id] = self.scope_version(scope_id) + 1
        global_version = self.__version
        scope_version = self.__scope_versions[scope_id]
        callback = self.__version_publication_callback
        if callable(callback):
            try:
                callback(global_version, scope_version)
            except Exception:
                pass
        return global_version, scope_version

    def set_version_publication_callback(
            self, callback: Optional[Callable[[int, int], None]],
    ) -> None:
        """Set one transient, best-effort callback before listener dispatch."""
        self.__version_publication_callback = callback if callable(callback) else None

    def active_progress_overlay(self, file_id: str) -> Optional[ActiveProgressOverlay]:
        return self.__active_progress_overlays.get(file_id)

    def active_progress_overlays_snapshot(self) -> Dict[str, ActiveProgressOverlay]:
        """Return a value snapshot for an atomic replacement transaction."""
        return dict(self.__active_progress_overlays)

    def active_progress_overlay_job_identities_snapshot(self) -> dict[str, tuple[int, str]]:
        """Return the live overlay job identities for one updater transaction."""
        return dict(self.__active_progress_overlay_job_identities)

    def apply_with_active_progress_retained(
            self,
            operation: Callable[[], object],
            retire_file_ids: Optional[Set[str]] = None,
    ) -> object:
        """Apply a replacement mutation without publishing an interim base.

        ModelUpdater uses this only after it has admitted the same live job
        for restoration across an authoritative replacement.  The normal
        mutation methods still own their notifications; their publication
        snapshots retain admitted overlays until the explicit restore or
        retirement decision immediately following the replacement.  Selected
        roots in ``retire_file_ids`` are removed before the operation so a
        replacement cannot publish their stale live projection while unrelated
        roots remain retained.
        """
        retired_file_ids = {
            file_id for file_id in (retire_file_ids or set())
            if isinstance(file_id, str)
        }
        retained_before = {
            file_id: self.__full_effective_file(file)
            for file_id, file in self.__files_by_id.items()
            if file_id in self.__active_progress_overlays
        }
        for file_id in retired_file_ids:
            self.__active_progress_overlays.pop(file_id, None)
            self.__active_progress_overlay_job_identities.pop(file_id, None)
            self.__legacy_overlay_correction_file_ids.discard(file_id)
            self.__legacy_overlay_correction_old_files.pop(file_id, None)
        self.__suppress_active_progress_clear += 1
        try:
            return operation()
        finally:
            self.__suppress_active_progress_clear -= 1
            for file_id in sorted(retired_file_ids):
                file = self.__files_by_id.get(file_id)
                if file is None:
                    continue
                previous_published = self.__published_files_by_id.get(file_id)
                new_published = self.__refresh_published_file(file)
                if previous_published is not None and previous_published == new_published:
                    continue
                global_version, scope_version = self.__advance_version(file)
                self.__notify_versioned_change(file, global_version, scope_version)
            for file_id, old_file in retained_before.items():
                if file_id in self.__active_progress_overlays and file_id in self.__files_by_id:
                    self.__legacy_overlay_correction_file_ids.add(file_id)
                    self.__legacy_overlay_correction_old_files[file_id] = old_file

    def clear_active_progress_overlays(self) -> None:
        """Discard live-only values and publish each affected root's base state."""
        if self.__suppress_active_progress_clear:
            return
        removed_file_ids = set(self.__active_progress_overlays)
        self.__active_progress_overlays = {}
        self.__active_progress_overlay_job_identities = {}
        for file_id in sorted(removed_file_ids):
            file = self.__files_by_id.get(file_id)
            if file is None:
                self.__notify_legacy_overlay_correction_if_needed(file_id, None)
                continue
            old_published = self.__published_files_by_id.get(file_id)
            published = self.__refresh_published_file(file)
            global_version, scope_version = self.__advance_version(file)
            self.__notify_legacy_overlay_correction_if_needed(file_id, file)
            self.__notify_versioned_change(file, global_version, scope_version)

    def clear_active_progress_overlays_except(self, preserved_file_ids: Set[str]) -> set[str]:
        """Clear only live projections outside one protected root set.

        The updater holds the Model lock while calling this method.  A stale
        status for one root must not retire a newer projection for another
        root in the same status poll.
        """
        preserved = {
            file_id for file_id in preserved_file_ids
            if isinstance(file_id, str)
        }
        removed_file_ids = set(self.__active_progress_overlays).difference(preserved)
        for file_id in removed_file_ids:
            self.__active_progress_overlays.pop(file_id, None)
            self.__active_progress_overlay_job_identities.pop(file_id, None)
        for file_id in sorted(removed_file_ids):
            file = self.__files_by_id.get(file_id)
            if file is None:
                self.__notify_legacy_overlay_correction_if_needed(file_id, None)
                continue
            old_published = self.__published_files_by_id.get(file_id)
            published = self.__refresh_published_file(file)
            global_version, scope_version = self.__advance_version(file)
            self.__notify_legacy_overlay_correction_if_needed(file_id, file)
            self.__notify_versioned_change(file, global_version, scope_version)
        return removed_file_ids

    def restore_active_progress_overlays(
            self,
            overlays: Dict[str, ActiveProgressOverlay],
            job_identities: Dict[str, tuple[int, str]],
    ) -> set[str]:
        """Restore protected live projections after an unrelated reconciliation.

        Only roots that still satisfy the direct-projection authority fence are
        restored.  The updater holds the Model lock while calling this method.
        """
        normalized: dict[str, ActiveProgressOverlay] = {}
        normalized_identities: dict[str, tuple[int, str]] = {}
        for file_id, overlay in overlays.items():
            identity = job_identities.get(file_id)
            file = self.__files_by_id.get(file_id)
            if not isinstance(file_id, str) or not isinstance(overlay, ActiveProgressOverlay) or \
                    not isinstance(identity, tuple) or len(identity) != 2 or \
                    type(identity[0]) is not int or identity[0] < 0 or \
                    not isinstance(identity[1], str) or not identity[1] or \
                    file is None or file.state != ModelFile.State.DOWNLOADING or \
                    not file.is_stoppable or file.explicitly_stopped or \
                    file.display_size_total is not None or file.display_transferred_size is not None:
                continue
            normalized[file_id] = overlay
            normalized_identities[file_id] = identity
        changed_root_ids = set(self.__active_progress_overlays).union(normalized)
        changed = {
            file_id for file_id in changed_root_ids
            if self.__active_progress_overlays.get(file_id) != normalized.get(file_id) or
            self.__active_progress_overlay_job_identities.get(file_id) != normalized_identities.get(file_id)
        }
        self.__active_progress_overlays = normalized
        self.__active_progress_overlay_job_identities = normalized_identities
        for file_id in sorted(changed):
            file = self.__files_by_id.get(file_id)
            if file is None:
                self.__notify_legacy_overlay_correction_if_needed(file_id, None)
                continue
            old_published = self.__published_files_by_id.get(file_id)
            published = self.__refresh_published_file(file)
            global_version, scope_version = self.__advance_version(file)
            self.__notify_legacy_overlay_correction_if_needed(file_id, file)
            self.__notify_versioned_change(file, global_version, scope_version)
        return changed

    def clear_active_progress_overlay_if_job_identity_matches(
            self, file_id: str, job_identity: tuple[int, str],
    ) -> bool:
        """Clear one stale projection when it still belongs to ``job_identity``.

        The updater holds the Model lock while calling this method.  The
        identity fence makes replacement-job cleanup safe if a later
        publication has already installed a different root projection.
        """
        if self.__active_progress_overlay_job_identities.get(file_id) != job_identity:
            return False
        if file_id not in self.__active_progress_overlays:
            return False
        self.__active_progress_overlays.pop(file_id, None)
        self.__active_progress_overlay_job_identities.pop(file_id, None)
        file = self.__files_by_id.get(file_id)
        if file is None:
            self.__notify_legacy_overlay_correction_if_needed(file_id, None)
            return True
        old_published = self.__published_files_by_id.get(file_id)
        published = self.__refresh_published_file(file)
        global_version, scope_version = self.__advance_version(file)
        self.__notify_legacy_overlay_correction_if_needed(file_id, file)
        self.__notify_versioned_change(file, global_version, scope_version)
        return True

    def replace_active_progress_overlays(
            self, overlays: Dict[str, ActiveProgressOverlay], changed_root_ids: Set[str],
    ) -> set[str]:
        """Atomically replace live-only progress and notify scoped readers.

        Ordinary progress pulses intentionally do not call the legacy
        ``file_updated`` contract: that path requires recursive immutable
        records.  A retained-overlay lifecycle retirement is the exception
        and emits one corrective full-tree update through the clear path.
        """
        normalized = {
            file_id: overlay for file_id, overlay in overlays.items()
            if isinstance(file_id, str) and isinstance(overlay, ActiveProgressOverlay)
        }
        changed = {
            file_id for file_id in changed_root_ids
            if file_id in self.__files_by_id and
            self.__active_progress_overlays.get(file_id) != normalized.get(file_id)
        }
        self.__active_progress_overlays = normalized
        self.__active_progress_overlay_job_identities = {}
        for file_id in sorted(changed):
            file = self.__files_by_id[file_id]
            old_published = self.__published_files_by_id.get(file_id)
            published = self.__refresh_published_file(file)
            global_version, scope_version = self.__advance_version(file)
            self.__notify_legacy_overlay_correction_if_needed(file_id, file)
            self.__notify_versioned_change(file, global_version, scope_version)
        return changed

    def publish_active_lftp_root_counters(
            self, overlays: Dict[str, ActiveProgressOverlay],
            job_identities: Dict[str, tuple[int, str]],
            lifecycle_epoch_matches: Callable[[str], bool],
    ) -> tuple[set[str], str]:
        """Publish fresh LFTP counters only onto already-authoritative roots.

        This is intentionally narrower than general overlay replacement.  It
        cannot establish download state, actions, topology, or display-union
        values; a failed admission clears the projection and leaves those
        concerns to the normal model reconciliation path.
        """
        normalized: dict[str, ActiveProgressOverlay] = {}
        normalized_identities: dict[str, tuple[int, str]] = {}
        identity_mismatches: dict[str, tuple[int, str]] = {}
        identity_rejected = False
        for file_id, overlay in overlays.items():
            identity = job_identities.get(file_id)
            file = self.__files_by_id.get(file_id)
            if not isinstance(file_id, str) or not isinstance(overlay, ActiveProgressOverlay) or \
                    not isinstance(identity, tuple) or len(identity) != 2 or \
                    type(identity[0]) is not int or not isinstance(identity[1], str) or \
                    identity[0] < 0 or not identity[1] or file is None:
                self.clear_active_progress_overlays()
                return set(), "job_identity"
            if not callable(lifecycle_epoch_matches) or not lifecycle_epoch_matches(file_id):
                self.clear_active_progress_overlays()
                return set(), "lifecycle_epoch"
            previous_identity = self.__active_progress_overlay_job_identities.get(file_id)
            if previous_identity is not None and previous_identity != identity:
                identity_rejected = True
                # LFTP job ids are monotonic within the controller lifecycle.
                # A newer replacement must retire the old projection; an
                # older late row must leave the newer projection untouched.
                if identity[0] > previous_identity[0]:
                    identity_mismatches[file_id] = previous_identity
                continue
            if file.state != ModelFile.State.DOWNLOADING or not file.is_stoppable or \
                    file.explicitly_stopped or file.display_size_total is not None or \
                    file.display_transferred_size is not None:
                self.clear_active_progress_overlays()
                return set(), "root_authority"
            prior_overlay = self.__active_progress_overlays.get(file_id)
            if previous_identity == identity and prior_overlay is not None:
                # A delayed positive counter from the same live job must not
                # repaint an already-published root backwards.  Zero and
                # None remain explicit reset/unknown values; rate metadata is
                # intentionally always taken from the incoming status.
                progress = overlay.download_progress
                if type(progress) is int and progress > 0 and \
                        type(prior_overlay.download_progress) is int:
                    progress = max(progress, prior_overlay.download_progress)
                transferred_size = overlay.transferred_size
                if type(transferred_size) is int and transferred_size > 0 and \
                        type(prior_overlay.transferred_size) is int:
                    transferred_size = max(transferred_size, prior_overlay.transferred_size)
                overlay = ActiveProgressOverlay(
                    progress, transferred_size, overlay.downloading_speed, overlay.eta,
                )
            normalized[file_id] = overlay
            normalized_identities[file_id] = identity

        if identity_rejected:
            for file_id, previous_identity in sorted(identity_mismatches.items()):
                self.clear_active_progress_overlay_if_job_identity_matches(
                    file_id, previous_identity,
                )
            return set(), "job_identity"

        changed_root_ids = set(self.__active_progress_overlays).union(normalized)
        changed = {
            file_id for file_id in changed_root_ids
            if self.__active_progress_overlays.get(file_id) != normalized.get(file_id) or
            self.__active_progress_overlay_job_identities.get(file_id) != normalized_identities.get(file_id)
        }
        self.__active_progress_overlays = normalized
        self.__active_progress_overlay_job_identities = normalized_identities
        for file_id in sorted(changed):
            file = self.__files_by_id.get(file_id)
            if file is None:
                self.__notify_legacy_overlay_correction_if_needed(file_id, None)
                continue
            old_published = self.__published_files_by_id.get(file_id)
            published = self.__refresh_published_file(file)
            global_version, scope_version = self.__advance_version(file)
            self.__notify_legacy_overlay_correction_if_needed(file_id, file)
            self.__notify_versioned_change(file, global_version, scope_version)
        return changed, "accepted"

    def set_base_logger(self, base_logger: logging.Logger) -> None:
        self.logger = base_logger.getChild("Model")

    @staticmethod
    def __format_file_for_log(file: ModelFile) -> str:
        path_pair_id = getattr(file, "path_pair_id", None)
        if path_pair_id:
            return "{} [{}]".format(file.name, path_pair_id[:8])
        return file.name

    def add_listener(self, listener: IModelListener) -> None:
        """
        Add a model listener
        :param listener:
        :return:
        """
        self.logger.debug("LftpModel: Adding a listener")
        with self.__listeners_lock:
            if listener not in self.__listeners:
                self.__listeners.append(listener)

    def remove_listener(self, listener: IModelListener) -> None:
        """
        Add a model listener
        :param listener:
        :return:
        """
        self.logger.debug("LftpModel: Removing a listener")
        with self.__listeners_lock:
            if listener not in self.__listeners:
                self.logger.error("LftpModel: listener does not exist!")
            else:
                self.__listeners.remove(listener)

    def add_file(self, file: ModelFile) -> None:
        """
        Add a file to the model
        :param file:
        :return:
        """
        self.clear_active_progress_overlays()
        self.logger.debug("LftpModel: Adding file '{}'".format(self.__format_file_for_log(file)))
        file_id = file.file_id
        if file_id in self.__files_by_id:
            raise ModelError("File already exists in the model")
        self.__files_by_id[file_id] = file
        insort(self.__ordered_file_ids, file_id)
        if file.name not in self.__file_ids_by_name:
            self.__file_ids_by_name[file.name] = set()
        self.__file_ids_by_name[file.name].add(file_id)
        published = self.__refresh_published_file(self.__files_by_id[file_id])
        legacy_published = self.__full_effective_file(self.__files_by_id[file_id])
        global_version, scope_version = self.__advance_version(file)
        with self.__listeners_lock:
            listeners = list(self.__listeners)
        for listener in listeners:
            listener.file_added(legacy_published)
        self.__notify_versioned_change(self.__files_by_id[file_id], global_version, scope_version)

    def __resolve_file_id(self, identifier: str) -> str:
        if identifier in self.__files_by_id:
            return identifier
        matching_ids = self.__file_ids_by_name.get(identifier)
        if not matching_ids:
            raise ModelError("File does not exist in the model")
        if len(matching_ids) > 1:
            raise ModelError("File lookup is ambiguous in the model")
        return next(iter(matching_ids))

    def remove_file(self, filename: str) -> None:
        """
        Remove the file from the model
        :param filename:
        :return:
        """
        self.clear_active_progress_overlays()
        file_id = self.__resolve_file_id(filename)
        file = self.__files_by_id[file_id]
        published = self.__published_files_by_id.get(file_id) or self.__refresh_published_file(file)
        legacy_published = self.__full_effective_file(file)
        self.logger.debug("LftpModel: Removing file '{}'".format(self.__format_file_for_log(file)))
        del self.__files_by_id[file_id]
        self.__ordered_file_ids.remove(file_id)
        self.__file_ids_by_name[file.name].remove(file_id)
        if not self.__file_ids_by_name[file.name]:
            del self.__file_ids_by_name[file.name]
        self.__published_files_by_id.pop(file_id, None)
        # A suppressed replacement can remove the root before its retained
        # overlay reaches a clear path.  It must never attach to a later root
        # that reuses the same canonical identity.
        self.__active_progress_overlays.pop(file_id, None)
        self.__active_progress_overlay_job_identities.pop(file_id, None)
        self.__legacy_overlay_correction_file_ids.discard(file_id)
        self.__legacy_overlay_correction_old_files.pop(file_id, None)
        global_version, scope_version = self.__advance_version(file)
        with self.__listeners_lock:
            listeners = list(self.__listeners)
        for listener in listeners:
            listener.file_removed(legacy_published)
        self.__notify_versioned_change(file, global_version, scope_version)

    def update_file(self, file: ModelFile) -> None:
        """
        Update an already existing file
        :param file:
        :return:
        """
        self.clear_active_progress_overlays()
        self.logger.debug("LftpModel: Updating file '{}'".format(self.__format_file_for_log(file)))
        file_id = file.file_id
        if file_id not in self.__files_by_id:
            raise ModelError("File does not exist in the model")
        old_file = self.__files_by_id[file_id]
        old_published = self.__published_files_by_id.get(file_id) or self.__refresh_published_file(old_file)
        legacy_old_published = self.__full_effective_file(old_file)
        new_file = file
        self.__files_by_id[file_id] = new_file
        new_published = self.__refresh_published_file(new_file)
        legacy_new_published = self.__full_effective_file(new_file)
        global_version, scope_version = self.__advance_version(new_file)
        with self.__listeners_lock:
            listeners = list(self.__listeners)
        for listener in listeners:
            listener.file_updated(legacy_old_published, legacy_new_published)
        self.__notify_versioned_change(new_file, global_version, scope_version)

    def get_file(self, name: str) -> ModelFile:
        """
        Returns a copy of the file of the given name
        :param name:
        :return:
        """
        return self.__files_by_id[self.__resolve_file_id(name)]

    def get_file_names(self) -> Set[str]:
        return set(self.__file_ids_by_name.keys())

    def get_file_ids(self) -> Set[str]:
        return set(self.__files_by_id.keys())
