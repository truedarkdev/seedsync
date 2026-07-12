# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import logging
from unittest.mock import MagicMock, patch
import sys
import multiprocessing
import ctypes
import threading
import time
import json

import pytest

from model import ModelFile
from controller.extract import ExtractDispatchError, ExtractFailedResult, ExtractProcess, ExtractListener, ExtractRequest, ExtractStatus
from common import MultiprocessingLogger
from common.breadcrumb_trace import BreadcrumbTraceCollector


pytestmark = pytest.mark.timeout(2)

class TestExtractProcess(unittest.TestCase):
    def setUp(self):
        dispatch_patcher = patch('controller.extract.extract_process.ExtractDispatch')
        self.addCleanup(dispatch_patcher.stop)
        self.mock_dispatch_cls = dispatch_patcher.start()
        self.mock_dispatch = self.mock_dispatch_cls.return_value

        # by default mock returns empty statuses
        self.mock_dispatch.status.return_value = []

        logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        # Assign process to this variable so that it can be cleaned up
        # even after an error
        self.process = None

    @pytest.mark.timeout(10)
    def test_real_spawn_with_production_breadcrumb_and_log_transport(self):
        collector = BreadcrumbTraceCollector(lambda: True)
        mp_logger = MultiprocessingLogger(logging.getLogger("extract-spawn-boundary"))
        process = ExtractProcess(
            out_dir_path="/tmp",
            local_path="/tmp",
            breadcrumb_trace=collector.create_emitter(),
        )
        process.set_mp_log_queue(mp_logger.queue, mp_logger.log_level)
        mp_logger.start()
        try:
            process.start()
            time.sleep(0.5)
            self.assertTrue(process.is_alive())
            process.terminate()
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
        finally:
            if process.is_alive():
                process.terminate()
                process.join()
            process.close_queues()
            mp_logger.stop()

    def tearDown(self):
        if self.process:
            self.process.terminate()

    def test_param_out_dir_path(self):
        self.out_dir_path = multiprocessing.Array(ctypes.c_char, 100)
        self.ctor_called = multiprocessing.Value('i', 0)

        def mock_ctor(**kwargs):
            self.out_dir_path.value = str.encode(kwargs["out_dir_path"])
            self.ctor_called.value = 1
            return self.mock_dispatch
        self.mock_dispatch_cls.side_effect = mock_ctor

        process = ExtractProcess(out_dir_path="/test/out/path",
                                 local_path="/test/local/path")
        process.run_init()
        # Wait for ctor to be called
        while self.ctor_called.value == 0:
            pass
        self.assertEqual("/test/out/path", self.out_dir_path.value.decode())

    def test_param_out_local_path(self):
        self.local_path = multiprocessing.Array(ctypes.c_char, 100)
        self.ctor_called = multiprocessing.Value('i', 0)

        def mock_ctor(**kwargs):
            self.local_path.value = str.encode(kwargs["local_path"])
            self.ctor_called.value = 1
            return self.mock_dispatch
        self.mock_dispatch_cls.side_effect = mock_ctor

        process = ExtractProcess(out_dir_path="/test/out/path",
                                 local_path="/test/local/path")
        process.run_init()
        # Wait for ctor to be called
        while self.ctor_called.value == 0:
            pass
        self.assertEqual("/test/local/path", self.local_path.value.decode())

    def test_param_out_local_path_fallback(self):
        self.local_path_fallback = multiprocessing.Array(ctypes.c_char, 100)
        self.ctor_called = multiprocessing.Value('i', 0)

        def mock_ctor(**kwargs):
            self.local_path_fallback.value = str.encode(kwargs["local_path_fallback"])
            self.ctor_called.value = 1
            return self.mock_dispatch
        self.mock_dispatch_cls.side_effect = mock_ctor

        process = ExtractProcess(out_dir_path="/test/out/path",
                                 local_path="/test/local/path",
                                 local_path_fallback="/test/local/fallback")
        process.run_init()
        while self.ctor_called.value == 0:
            pass
        self.assertEqual("/test/local/fallback", self.local_path_fallback.value.decode())

    def test_calls_start_dispatch(self):
        self.start_called = multiprocessing.Value('i', 0)

        def _start():
            self.start_called.value = 1
        self.mock_dispatch.start.side_effect = _start

        process = ExtractProcess(out_dir_path="/test/out/path",
                                 local_path="/test/local/path")
        process.run_init()
        while self.start_called.value == 0:
            pass

    def test_close_queues_releases_owned_queues_and_is_idempotent(self):
        exception_queue = MagicMock()
        command_queue = MagicMock()
        status_queue = MagicMock()
        completed_queue = MagicMock()
        failed_queue = MagicMock()

        with patch(
            "controller.extract.extract_process.multiprocessing.Queue",
            side_effect=[exception_queue, command_queue, status_queue, completed_queue, failed_queue],
        ):
            process = ExtractProcess(out_dir_path="/test/out/path", local_path="/test/local/path")

        process.close_queues()
        process.close_queues()

        for queue in (exception_queue, command_queue, status_queue, completed_queue, failed_queue):
            queue.close.assert_called_once_with()
            queue.join_thread.assert_called_once_with()
        self.assertIsNone(process._ExtractProcess__command_queue)
        self.assertIsNone(process._ExtractProcess__status_result_queue)
        self.assertIsNone(process._ExtractProcess__completed_result_queue)
        self.assertIsNone(process._ExtractProcess__failed_result_queue)
        self.assertIsNone(process._AppProcess__exception_queue)
        self.assertIsNone(process._terminate)
        self.assertIsNone(process._mp_log_queue)
        self.assertIsNone(process._mp_log_level)

    @pytest.mark.timeout(10)
    def test_retrieves_status(self):
        # Use this as a signal to mock to control which status to send
        self.status_signal = multiprocessing.Value('i', 0)
        self.status_counter = multiprocessing.Value('i', 0)

        s_a = ExtractStatus(name="a", is_dir=True, state=ExtractStatus.State.EXTRACTING)
        s_b = ExtractStatus(name="b", is_dir=False, state=ExtractStatus.State.EXTRACTING)
        s_c = ExtractStatus(name="c", is_dir=True, state=ExtractStatus.State.EXTRACTING)

        def _status():
            ret = None
            if self.status_signal.value == 0:
                ret = [s_a]
            elif self.status_signal.value == 1:
                ret = [s_a, s_b]
            elif self.status_signal.value == 2:
                ret = [s_c]
            elif self.status_signal.value == 3:
                ret = []
            self.status_counter.value += 1
            return ret
        self.mock_dispatch.status.side_effect = _status

        process = ExtractProcess(out_dir_path="", local_path="")
        process.run_init()
        process.run_loop()

        # wait for the first queued status snapshot
        while self.status_counter.value < 1:
            pass
        status_result = process.pop_latest_statuses()
        self.assertEqual(1, len(status_result.statuses))
        self.assertEqual("a", status_result.statuses[0].name)
        self.assertEqual(True, status_result.statuses[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status_result.statuses[0].state)

        # signal for status #1 and wait status fetch
        self.status_signal.value = 1
        process.run_loop()
        while self.status_counter.value < 2:
            pass
        status_result = process.pop_latest_statuses()
        self.assertEqual(2, len(status_result.statuses))
        self.assertEqual("a", status_result.statuses[0].name)
        self.assertEqual(True, status_result.statuses[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status_result.statuses[0].state)
        self.assertEqual("b", status_result.statuses[1].name)
        self.assertEqual(False, status_result.statuses[1].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status_result.statuses[1].state)

        # signal for status #2 and wait status fetch
        self.status_signal.value = 2
        process.run_loop()
        while self.status_counter.value < 3:
            pass
        status_result = process.pop_latest_statuses()
        self.assertEqual(1, len(status_result.statuses))
        self.assertEqual("c", status_result.statuses[0].name)
        self.assertEqual(True, status_result.statuses[0].is_dir)
        self.assertEqual(ExtractStatus.State.EXTRACTING, status_result.statuses[0].state)

        # signal for status #3 and wait status fetch
        self.status_signal.value = 3
        process.run_loop()
        while self.status_counter.value < 4:
            pass
        status_result = process.pop_latest_statuses()
        self.assertEqual(0, len(status_result.statuses))

    @pytest.mark.timeout(10)
    def test_retrieves_completed(self):
        # Use this as a signal to mock to control which completed list to send
        self.completed_signal = multiprocessing.Value('i', 0)
        self.completed_counter = multiprocessing.Value('i', 0)

        def _add_listener(listener: ExtractListener):
            print("Listener added")

            def _callback_sequence():
                listener.extract_completed(name="a", is_dir=True, file_id="a-id", path_pair_id="pair-1")
                time.sleep(0.1)
                self.completed_signal.value = 1

                time.sleep(1.0)
                listener.extract_completed(name="b", is_dir=False)
                listener.extract_completed(name="c", is_dir=True)
                time.sleep(0.1)
                self.completed_signal.value = 2

            threading.Thread(target=_callback_sequence).start()
        self.mock_dispatch.add_listener.side_effect = _add_listener

        process = ExtractProcess(out_dir_path="", local_path="")
        process.run_init()

        while self.completed_signal.value < 1:
            pass
        completed = process.pop_completed()
        self.assertEqual(1, len(completed))
        self.assertEqual("a", completed[0].name)
        self.assertEqual(True, completed[0].is_dir)
        self.assertEqual("a-id", completed[0].file_id)
        self.assertEqual("pair-1", completed[0].path_pair_id)
        # next one should be empty
        completed = process.pop_completed()
        self.assertEqual(0, len(completed))

        while self.completed_signal.value < 2:
            pass
        completed = process.pop_completed()
        self.assertEqual(2, len(completed))
        self.assertEqual("b", completed[0].name)
        self.assertEqual(False, completed[0].is_dir)
        self.assertEqual("c", completed[1].name)
        self.assertEqual(True, completed[1].is_dir)
        # next one should be empty
        completed = process.pop_completed()
        self.assertEqual(0, len(completed))

    def test_extract_listener_logs_target_archive_trace_on_failure(self):
        process = ExtractProcess(out_dir_path="", local_path="")
        process._ExtractProcess__target_archive_trace_file_id = "archive.zip"
        process._ExtractProcess__target_archive_trace_logger = MagicMock()
        process._ExtractProcess__target_archive_trace_last_signature = None
        failed_queue = MagicMock()

        listener = ExtractProcess._ExtractProcess__ExtractListener(
            logger=MagicMock(),
            completed_queue=MagicMock(),
            failed_queue=failed_queue,
            trace_owner=process
        )

        listener.extract_failed("archive.zip", False, file_id="archive.zip", path_pair_id="pair-1")

        process._ExtractProcess__target_archive_trace_logger.info.assert_called_once()
        payload = json.loads(process._ExtractProcess__target_archive_trace_logger.info.call_args[0][1])
        self.assertEqual("extract_failed", payload["event"])
        self.assertEqual("archive.zip", payload["file_name"])
        failed_queue.put.assert_called_once()
        failed_result = failed_queue.put.call_args.args[0]
        self.assertIsInstance(failed_result, ExtractFailedResult)
        self.assertEqual("archive.zip", failed_result.name)
        self.assertEqual(False, failed_result.is_dir)
        self.assertEqual("archive.zip", failed_result.file_id)
        self.assertEqual("pair-1", failed_result.path_pair_id)

    @pytest.mark.timeout(5)
    def test_forwards_extract_commands(self):
        a = ModelFile("a", True)
        a.local_size = 100
        aa = ModelFile("aa", False)
        aa.local_size = 60
        a.add_child(aa)
        ab = ModelFile("ab", False)
        ab.local_size = 40
        a.add_child(ab)

        b = ModelFile("b", True)
        b.local_size = 10
        ba = ModelFile("ba", True)
        ba.local_size = 10
        b.add_child(ba)
        baa = ModelFile("baa", False)
        baa.local_size = 10
        ba.add_child(baa)

        c = ModelFile("c", False)
        c.local_size = 1234

        self.extract_counter = multiprocessing.Value('i', 0)

        def _extract(file: ModelFile):
            print(file.name)
            if self.extract_counter.value == 0:
                self.assertEqual("a", file.name)
                self.assertEqual(True, file.is_dir)
                self.assertEqual(100, file.local_size)
                children = file.get_children()
                self.assertEqual(2, len(children))
                self.assertEqual("aa", children[0].name)
                self.assertEqual(False, children[0].is_dir)
                self.assertEqual(60, children[0].local_size)
                self.assertEqual("ab", children[1].name)
                self.assertEqual(False, children[0].is_dir)
                self.assertEqual(40, children[1].local_size)
            elif self.extract_counter.value == 1:
                self.assertEqual("b", file.name)
                self.assertEqual(True, file.is_dir)
                self.assertEqual(10, file.local_size)
                self.assertEqual(1, len(file.get_children()))
                child = file.get_children()[0]
                self.assertEqual("ba", child.name)
                self.assertEqual(True, child.is_dir)
                self.assertEqual(10, child.local_size)
                self.assertEqual(1, len(child.get_children()))
                subchild = child.get_children()[0]
                self.assertEqual("baa", subchild.name)
                self.assertEqual(False, subchild.is_dir)
                self.assertEqual(10, subchild.local_size)
            elif self.extract_counter.value == 2:
                self.assertEqual("c", file.name)
                self.assertEqual(False, file.is_dir)
                self.assertEqual(1234, file.local_size)
            self.extract_counter.value += 1
        self.mock_dispatch.extract.side_effect = _extract

        process = ExtractProcess(out_dir_path="", local_path="")
        process.run_init()

        process.extract(a)
        time.sleep(0.05)
        process.run_loop()
        self.assertEqual(1, self.extract_counter.value)

        process.extract(b)
        time.sleep(0.05)
        process.run_loop()
        self.assertEqual(2, self.extract_counter.value)

        process.extract(c)
        time.sleep(0.05)
        process.run_loop()
        self.assertEqual(3, self.extract_counter.value)

    def test_extract_records_queue_dequeue_dispatch_lifecycle_with_flow_id(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        process = ExtractProcess(out_dir_path="", local_path="", breadcrumb_trace=collector.create_emitter())
        process.run_init()

        file = ModelFile("archive.zip", False)
        file.path_pair_id = "pair-1"
        file.local_size = 100

        process.extract(file, flow_id="flow-123")
        time.sleep(0.05)
        process.run_loop()

        entries = [
            entry for entry in collector.snapshot()["entries"]
            if entry["message"] in {"extract_command_queued", "extract_command_dequeued", "extract_command_dispatched"}
        ]
        self.assertEqual(
            ["extract_command_queued", "extract_command_dequeued", "extract_command_dispatched"],
            [entry["message"] for entry in entries]
        )
        self.assertEqual(["flow-123", "flow-123", "flow-123"], [entry["flow_id"] for entry in entries])
        self.assertEqual(["pair-1", "pair-1", "pair-1"], [entry["corr_id"] for entry in entries])

    def test_extract_accepts_extract_request_and_forwards_it_through_dispatch(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        process = ExtractProcess(out_dir_path="", local_path="", breadcrumb_trace=collector.create_emitter())
        process.run_init()

        model_file = ModelFile("archive.zip", False)
        model_file.path_pair_id = "pair-1"
        model_file.local_size = 100
        request = ExtractRequest(
            model_file=model_file,
            local_path="/staging/pair-1/incomplete",
            out_dir_path="/staging/pair-1/incomplete",
            pair_id="pair-1",
            local_path_fallback="/local/pair-1",
            out_dir_path_fallback="/extract",
        )

        process.extract(request, flow_id="flow-request")
        time.sleep(0.05)
        process.run_loop()

        self.mock_dispatch.extract.assert_called_once()
        dispatched_request = self.mock_dispatch.extract.call_args.args[0]
        self.assertIsInstance(dispatched_request, ExtractRequest)
        self.assertEqual("pair-1", dispatched_request.pair_id)
        self.assertEqual("/staging/pair-1/incomplete", dispatched_request.local_path)
        self.assertEqual("/staging/pair-1/incomplete", dispatched_request.out_dir_path)
        self.assertEqual("/local/pair-1", dispatched_request.local_path_fallback)
        self.assertEqual("/extract", dispatched_request.out_dir_path_fallback)
        self.assertEqual("archive.zip", dispatched_request.model_file.name)
        self.assertEqual("pair-1", dispatched_request.model_file.path_pair_id)
        entries = [
            entry for entry in collector.snapshot()["entries"]
            if entry["message"] in {"extract_command_queued", "extract_command_dequeued", "extract_command_dispatched"}
        ]
        self.assertEqual(
            ["extract_command_queued", "extract_command_dequeued", "extract_command_dispatched"],
            [entry["message"] for entry in entries]
        )
        self.assertEqual(["flow-request", "flow-request", "flow-request"], [entry["flow_id"] for entry in entries])
        self.assertEqual(["pair-1", "pair-1", "pair-1"], [entry["corr_id"] for entry in entries])

    def test_extract_completion_breadcrumb_reuses_inflight_flow_id(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        captured_listener = {"value": None}

        def _add_listener(listener: ExtractListener):
            captured_listener["value"] = listener

        self.mock_dispatch.add_listener.side_effect = _add_listener
        process = ExtractProcess(out_dir_path="", local_path="", breadcrumb_trace=collector.create_emitter())
        process.run_init()

        file = ModelFile("archive.zip", False)
        file.path_pair_id = "pair-1"
        file.local_size = 100
        process.extract(file, flow_id="flow-abc")
        time.sleep(0.05)
        process.run_loop()

        captured_listener["value"].extract_completed(
            name="archive.zip",
            is_dir=False,
            file_id=file.file_id,
            path_pair_id="pair-1",
        )

        completion_entries = [
            entry for entry in collector.snapshot()["entries"]
            if entry["message"] == "extract_completed"
        ]
        self.assertEqual(1, len(completion_entries))
        self.assertEqual("flow-abc", completion_entries[0]["flow_id"])
        self.assertEqual("pair-1", completion_entries[0]["corr_id"])

    def test_extract_completion_keeps_flow_id_when_callback_fires_before_dispatch_returns(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        captured_listener = {"value": None}

        def _add_listener(listener: ExtractListener):
            captured_listener["value"] = listener

        def _extract(file: ModelFile):
            captured_listener["value"].extract_completed(
                name=file.name,
                is_dir=file.is_dir,
                file_id=file.file_id,
                path_pair_id=file.path_pair_id,
            )

        self.mock_dispatch.add_listener.side_effect = _add_listener
        self.mock_dispatch.extract.side_effect = _extract

        process = ExtractProcess(out_dir_path="", local_path="", breadcrumb_trace=collector.create_emitter())
        process.run_init()

        file = ModelFile("archive.zip", False)
        file.path_pair_id = "pair-1"
        file.local_size = 100

        process.extract(file, flow_id="flow-race")
        time.sleep(0.05)
        process.run_loop()

        completion_entries = [
            entry for entry in collector.snapshot()["entries"]
            if entry["message"] == "extract_completed"
        ]
        self.assertEqual(1, len(completion_entries))
        self.assertEqual("flow-race", completion_entries[0]["flow_id"])
        self.assertEqual("pair-1", completion_entries[0]["corr_id"])
        self.assertIsNone(
            process._ExtractProcess__pop_inflight_flow_id(file_id=file.file_id, file_name=file.name)
        )

    def test_extract_dispatch_failure_rolls_back_preregistered_flow_id(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.mock_dispatch.extract.side_effect = Exception("boom")

        process = ExtractProcess(out_dir_path="", local_path="", breadcrumb_trace=collector.create_emitter())
        process.run_init()

        file = ModelFile("archive.zip", False)
        file.local_size = 100

        process.extract(file, flow_id="flow-blocked")

        with self.assertRaises(Exception):
            process.run_loop()

        self.assertIsNone(
            process._ExtractProcess__pop_inflight_flow_id(file_id=file.file_id, file_name=file.name)
        )

    def test_extract_dispatch_error_emits_failed_result(self):
        collector = BreadcrumbTraceCollector(lambda: True, max_entries=16)
        self.mock_dispatch.extract.side_effect = ExtractDispatchError("boom")

        process = ExtractProcess(out_dir_path="", local_path="", breadcrumb_trace=collector.create_emitter())
        process.run_init()

        file = ModelFile("archive.zip", False)
        file.path_pair_id = "pair-1"
        file.local_size = 100

        process.extract(file, flow_id="flow-blocked")
        time.sleep(0.05)
        process.run_loop()

        failed = process.pop_failed()
        self.assertEqual(1, len(failed))
        self.assertEqual("archive.zip", failed[0].name)
        self.assertEqual(False, failed[0].is_dir)
        self.assertEqual(file.file_id, failed[0].file_id)
        self.assertEqual("pair-1", failed[0].path_pair_id)
        self.assertEqual(0, len(process.pop_failed()))
        self.assertIsNone(
            process._ExtractProcess__pop_inflight_flow_id(file_id=file.file_id, file_name=file.name)
        )
