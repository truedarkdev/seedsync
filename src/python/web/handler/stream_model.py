# Copyright 2017, Inderpreet Singh, All rights reserved.

import time
from threading import Event
from typing import Callable, Optional

from ..web_app import IStreamHandler
from ..utils import StreamQueue
from ..serialize import SerializeModel
from model import IModelListener, ModelFile
from common import overrides
from controller.controller import Controller


class WebResponseModelListener(IModelListener, StreamQueue[SerializeModel.UpdateEvent]):
    """
    Model listener used by streams to listen to model updates
    One listener should be created for each new request
    """
    def __init__(self, trace_metadata_provider: Optional[Callable[[ModelFile], Optional[dict[str, object]]]] = None):
        super().__init__()
        self.__trace_metadata_provider = trace_metadata_provider
        self.__trace_breadcrumb_callback: Optional[
            Callable[[str, Optional[ModelFile], Optional[dict[str, object]]], None]
        ] = None

    def set_trace_breadcrumb_callback(
        self,
        callback: Optional[Callable[[str, Optional[ModelFile], Optional[dict[str, object]]], None]],
    ) -> None:
        self.__trace_breadcrumb_callback = callback

    def __queue_event(
        self,
        change: SerializeModel.UpdateEvent.Change,
        old_file: Optional[ModelFile],
        new_file: Optional[ModelFile],
    ) -> None:
        traced_file = new_file if new_file is not None else old_file
        trace_metadata = None
        if traced_file is not None and self.__trace_metadata_provider is not None:
            try:
                provided_metadata = self.__trace_metadata_provider(traced_file)
                if isinstance(provided_metadata, dict):
                    trace_metadata = provided_metadata
            except Exception:
                # Optional diagnostics must never interrupt model delivery.
                trace_metadata = None
        event = SerializeModel.UpdateEvent(
            change=change,
            old_file=old_file,
            new_file=new_file,
            trace_metadata=trace_metadata,
        )
        if trace_metadata is not None:
            event.mark_enqueued(
                int(time.time_ns() / 1_000_000),
                self.get_queue_size(),
                self.get_dropped_count(),
            )
            callback = self.__trace_breadcrumb_callback
            if callback is not None:
                try:
                    callback("enqueue", traced_file, {
                        "enqueue_timestamp_ms": event.enqueue_timestamp_ms,
                        "queue_size_before_enqueue": event.queue_size_before_enqueue,
                        "queue_dropped_count_before_enqueue": event.queue_dropped_count_before_enqueue,
                        "published_progress": getattr(traced_file, "download_progress", None),
                        "published_transferred_size": getattr(traced_file, "transferred_size", None),
                        "published_state": getattr(getattr(traced_file, "state", None), "name", None),
                    })
                except Exception:
                    # Diagnostics must never affect listener delivery.
                    pass
        self.put(event)

    @overrides(IModelListener)
    def file_added(self, file: ModelFile):
        self.__queue_event(SerializeModel.UpdateEvent.Change.ADDED, None, file)

    @overrides(IModelListener)
    def file_removed(self, file: ModelFile):
        self.__queue_event(SerializeModel.UpdateEvent.Change.REMOVED, file, None)

    @overrides(IModelListener)
    def file_updated(self, old_file: ModelFile, new_file: ModelFile):
        self.__queue_event(SerializeModel.UpdateEvent.Change.UPDATED, old_file, new_file)


class ModelStreamHandler(IStreamHandler):
    def __init__(self, controller: Controller):
        self.controller = controller
        self.serialize = SerializeModel()
        self.model_listener = WebResponseModelListener(self.controller.get_stop_resume_trace_metadata)
        self.model_listener.set_trace_breadcrumb_callback(
            self.controller.record_stop_resume_trace_breadcrumb
        )
        self.initial_model_files: list[ModelFile] | None = None
        self.first_run = True
        self.__stream_emit_sequence = 0

    @overrides(IStreamHandler)
    def set_wake_event(self, wake_event: Event) -> None:
        self.model_listener.set_wake_event(wake_event)

    def __trace_enabled_at_emit(self) -> bool:
        """Check the live gate and fail closed if the diagnostic hook fails."""
        try:
            return bool(self.controller.is_stop_resume_trace_enabled())
        except Exception:
            return False

    @overrides(IStreamHandler)
    def setup(self):
        self.initial_model_files = self.controller.get_model_files_and_add_listener(self.model_listener)

    @overrides(IStreamHandler)
    def get_value(self) -> str | None:
        if self.first_run:
            self.first_run = False
            assert self.initial_model_files is not None
            return self.serialize.model(self.initial_model_files)
        else:
            event = self.model_listener.get_next_event()
            if event is not None:
                # A queued update may have been captured while tracing was on
                # and emitted after the setting was turned off.  Recheck the
                # shared gate immediately before serialization so disabled
                # responses never carry a stale trace field or do work for it.
                if event.trace_metadata is not None and not self.__trace_enabled_at_emit():
                    event.trace_metadata = None
                self.__stream_emit_sequence += 1
                event.mark_stream_emitted(
                    self.__stream_emit_sequence,
                    self.model_listener.get_queue_size(),
                    self.model_listener.get_dropped_count(),
                )
                if event.trace_metadata is not None:
                    traced_file = event.new_file if event.new_file is not None else event.old_file
                    try:
                        self.controller.record_stop_resume_trace_breadcrumb(
                            "emit",
                            traced_file,
                            {
                                "cycle": event.trace_metadata.get("cycle"),
                                "corr_id": event.trace_metadata.get("corr_id"),
                                "file_id": event.trace_metadata.get("file_id"),
                                "enqueue_timestamp_ms": event.trace_metadata.get("enqueue_timestamp_ms"),
                                "stream_emit_sequence": event.trace_metadata.get("stream_emit_sequence"),
                                "stream_emit_timestamp_ms": event.trace_metadata.get("stream_emit_timestamp_ms"),
                                "queue_wait_ms": event.trace_metadata.get("queue_wait_ms"),
                                "queue_size": event.trace_metadata.get("queue_size"),
                                "queue_dropped_count": event.trace_metadata.get("queue_dropped_count"),
                                "published_progress": getattr(traced_file, "download_progress", None),
                                "published_transferred_size": getattr(traced_file, "transferred_size", None),
                                "published_state": getattr(getattr(traced_file, "state", None), "name", None),
                            },
                        )
                    except Exception:
                        pass
                # Model listeners receive immutable effective-publication
                # snapshots.  Resolving overlays here would mix an old queued
                # lifecycle record with a later live-progress value.
                return self.serialize.update_event(event)
            else:
                return None

    @overrides(IStreamHandler)
    def cleanup(self):
        if self.model_listener:
            self.controller.remove_model_listener(self.model_listener)
