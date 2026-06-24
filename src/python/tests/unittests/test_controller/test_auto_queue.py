# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
from unittest.mock import MagicMock
from unittest.mock import patch
import logging
import sys
import json
import threading

from common import overrides, PersistError, Config
from controller import AutoQueue, AutoQueuePersist, IAutoQueuePersistListener, AutoQueuePattern
from controller.auto_queue import AutoQueuePersistListener
from controller import Controller
from model import IModelListener, ModelFile


class TestAutoQueuePattern(unittest.TestCase):
    def test_pattern(self):
        aqp = AutoQueuePattern(pattern="file.one")
        self.assertEqual(aqp.pattern, "file.one")
        aqp = AutoQueuePattern(pattern="file.two")
        self.assertEqual(aqp.pattern, "file.two")

    def test_equality(self):
        aqp_1 = AutoQueuePattern(pattern="file.one")
        aqp_2 = AutoQueuePattern(pattern="file.two")
        aqp_1b = AutoQueuePattern(pattern="file.one")
        self.assertEqual(aqp_1, aqp_1b)
        self.assertNotEqual(aqp_1, aqp_2)

    def test_to_str(self):
        self.assertEqual(
            "{\"pattern\": \"file.one\"}",
            AutoQueuePattern(pattern="file.one").to_str()
        )
        self.assertEqual(
            "{\"pattern\": \"file'one\"}",
            AutoQueuePattern(pattern="file'one").to_str()
        )
        self.assertEqual(
            "{\"pattern\": \"file\\\"one\"}",
            AutoQueuePattern(pattern="file\"one").to_str()
        )
        self.assertEqual(
            "{\"pattern\": \"fil(eo)ne\"}",
            AutoQueuePattern(pattern="fil(eo)ne").to_str()
        )

    def test_from_str(self):
        self.assertEqual(
            AutoQueuePattern(pattern="file.one"),
            AutoQueuePattern.from_str("{\"pattern\": \"file.one\"}"),
        )
        self.assertEqual(
            AutoQueuePattern(pattern="file'one"),
            AutoQueuePattern.from_str("{\"pattern\": \"file'one\"}"),
        )
        self.assertEqual(
            AutoQueuePattern(pattern="file\"one"),
            AutoQueuePattern.from_str("{\"pattern\": \"file\\\"one\"}"),
        )
        self.assertEqual(
            AutoQueuePattern(pattern="fil(eo)ne"),
            AutoQueuePattern.from_str("{\"pattern\": \"fil(eo)ne\"}"),
        )

    def test_to_and_from_str(self):
        self.assertEqual(
            AutoQueuePattern(pattern="file.one"),
            AutoQueuePattern.from_str(AutoQueuePattern(pattern="file.one").to_str())
        )
        self.assertEqual(
            AutoQueuePattern(pattern="file'one"),
            AutoQueuePattern.from_str(AutoQueuePattern(pattern="file'one").to_str())
        )
        self.assertEqual(
            AutoQueuePattern(pattern="file\"one"),
            AutoQueuePattern.from_str(AutoQueuePattern(pattern="file\"one").to_str())
        )
        self.assertEqual(
            AutoQueuePattern(pattern="fil(eo)ne"),
            AutoQueuePattern.from_str(AutoQueuePattern(pattern="fil(eo)ne").to_str())
        )


class TestAutoQueuePersistListener(IAutoQueuePersistListener):
    @overrides(IAutoQueuePersistListener)
    def pattern_added(self, pattern: AutoQueuePattern):
        pass

    @overrides(IAutoQueuePersistListener)
    def pattern_removed(self, pattern: AutoQueuePattern):
        pass


class TestAutoQueuePersist(unittest.TestCase):
    def test_add_pattern(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        self.assertEqual({
            AutoQueuePattern(pattern="one"),
            AutoQueuePattern(pattern="two")
        }, persist.patterns)
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="three"))
        self.assertEqual({
            AutoQueuePattern(pattern="one"),
            AutoQueuePattern(pattern="two"),
            AutoQueuePattern(pattern="three")
        }, persist.patterns)

    def test_add_blank_pattern_fails(self):
        persist = AutoQueuePersist()
        with self.assertRaises(ValueError):
            persist.add_pattern(AutoQueuePattern(pattern=""))
        with self.assertRaises(ValueError):
            persist.add_pattern(AutoQueuePattern(pattern=" "))
        with self.assertRaises(ValueError):
            persist.add_pattern(AutoQueuePattern(pattern="   "))

    def test_remove_pattern(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        persist.remove_pattern(AutoQueuePattern(pattern="one"))
        self.assertEqual({AutoQueuePattern(pattern="two")}, persist.patterns)
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="three"))
        persist.remove_pattern(AutoQueuePattern(pattern="two"))
        self.assertEqual({
            AutoQueuePattern(pattern="one"),
            AutoQueuePattern(pattern="three")
        }, persist.patterns)

    def test_listener_pattern_added(self):
        listener = TestAutoQueuePersistListener()
        listener.pattern_added = MagicMock()
        persist = AutoQueuePersist()
        persist.add_listener(listener)
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        listener.pattern_added.assert_called_once_with(AutoQueuePattern(pattern="one"))
        listener.pattern_added.reset_mock()
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        listener.pattern_added.assert_called_once_with(AutoQueuePattern(pattern="two"))
        listener.pattern_added.reset_mock()

    def test_listener_pattern_added_duplicate(self):
        listener = TestAutoQueuePersistListener()
        listener.pattern_added = MagicMock()
        persist = AutoQueuePersist()
        persist.add_listener(listener)
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        listener.pattern_added.assert_called_once_with(AutoQueuePattern(pattern="one"))
        listener.pattern_added.reset_mock()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        listener.pattern_added.assert_not_called()

    def test_listener_pattern_removed(self):
        listener = TestAutoQueuePersistListener()
        listener.pattern_removed = MagicMock()
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        persist.add_pattern(AutoQueuePattern(pattern="three"))
        persist.add_listener(listener)
        persist.remove_pattern(AutoQueuePattern(pattern="one"))
        listener.pattern_removed.assert_called_once_with(AutoQueuePattern(pattern="one"))
        listener.pattern_removed.reset_mock()
        persist.remove_pattern(AutoQueuePattern(pattern="two"))
        listener.pattern_removed.assert_called_once_with(AutoQueuePattern(pattern="two"))
        listener.pattern_removed.reset_mock()

    def test_listener_pattern_removed_non_existing(self):
        listener = TestAutoQueuePersistListener()
        listener.pattern_removed = MagicMock()
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        persist.add_pattern(AutoQueuePattern(pattern="three"))
        persist.add_listener(listener)
        persist.remove_pattern(AutoQueuePattern(pattern="four"))
        listener.pattern_removed.assert_not_called()

    def test_from_str(self):
        content = """
        {{
            "patterns": [
                "{}",
                "{}",
                "{}",
                "{}",
                "{}",
                "{}"
            ]
        }}
        """.format(
            AutoQueuePattern(pattern="one").to_str().replace("\\", "\\\\").replace("\"", "\\\""),
            AutoQueuePattern(pattern="two").to_str().replace("\\", "\\\\").replace("\"", "\\\""),
            AutoQueuePattern(pattern="th ree").to_str().replace("\\", "\\\\").replace("\"", "\\\""),
            AutoQueuePattern(pattern="fo.ur").to_str().replace("\\", "\\\\").replace("\"", "\\\""),
            AutoQueuePattern(pattern="fi\"ve").to_str().replace("\\", "\\\\").replace("\"", "\\\""),
            AutoQueuePattern(pattern="si'x").to_str().replace("\\", "\\\\").replace("\"", "\\\"")
        )
        print(content)
        print(AutoQueuePattern(pattern="fi\"ve").to_str())
        persist = AutoQueuePersist.from_str(content)
        golden_patterns = {
            AutoQueuePattern(pattern="one"),
            AutoQueuePattern(pattern="two"),
            AutoQueuePattern(pattern="th ree"),
            AutoQueuePattern(pattern="fo.ur"),
            AutoQueuePattern(pattern="fi\"ve"),
            AutoQueuePattern(pattern="si'x")
        }
        self.assertEqual(golden_patterns, persist.patterns)

    def test_to_str(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        persist.add_pattern(AutoQueuePattern(pattern="th ree"))
        persist.add_pattern(AutoQueuePattern(pattern="fo.ur"))
        persist.add_pattern(AutoQueuePattern(pattern="fi\"ve"))
        persist.add_pattern(AutoQueuePattern(pattern="si'x"))
        print(persist.to_str())
        dct = json.loads(persist.to_str())
        self.assertTrue("patterns" in dct)
        self.assertEqual(
            [
                AutoQueuePattern(pattern="one").to_str(),
                AutoQueuePattern(pattern="two").to_str(),
                AutoQueuePattern(pattern="th ree").to_str(),
                AutoQueuePattern(pattern="fo.ur").to_str(),
                AutoQueuePattern(pattern="fi\"ve").to_str(),
                AutoQueuePattern(pattern="si'x").to_str()
            ],
            dct["patterns"]
        )

    def test_to_and_from_str(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        persist.add_pattern(AutoQueuePattern(pattern="th ree"))
        persist.add_pattern(AutoQueuePattern(pattern="fo.ur"))
        persist.add_pattern(AutoQueuePattern(pattern="fi\"ve"))
        persist.add_pattern(AutoQueuePattern(pattern="si'x"))

        persist_actual = AutoQueuePersist.from_str(persist.to_str())
        self.assertEqual(
            persist.patterns,
            persist_actual.patterns
        )

    def test_persist_read_error(self):
        # bad pattern
        content = """
        {
            "patterns": [
                "bad string"
            ]
        }
        """
        with self.assertRaises(PersistError):
            AutoQueuePersist.from_str(content)

        # empty json
        content = ""
        with self.assertRaises(PersistError):
            AutoQueuePersist.from_str(content)

        # missing keys
        content = "{}"
        with self.assertRaises(PersistError):
            AutoQueuePersist.from_str(content)

        # malformed
        content = "{"
        with self.assertRaises(PersistError):
            AutoQueuePersist.from_str(content)

    def test_from_str_rejects_malformed_shapes(self):
        with self.assertRaises(PersistError):
            AutoQueuePersist.from_str("""
            {
                "patterns": null
            }
            """)

        with self.assertRaises(PersistError):
            AutoQueuePersist.from_str("[]")

    def _assert_blocks_until_lock_released(self, lock, op, op_name):
        started = threading.Event()
        done = threading.Event()
        errors = []

        def run():
            started.set()
            try:
                op()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        thread = threading.Thread(target=run)
        thread.daemon = True
        with lock:
            thread.start()
            self.assertTrue(started.wait(1), f"{op_name}: worker thread never started")
            self.assertFalse(done.wait(0.1), f"{op_name}: operation did not block on the shared lock")
        thread.join(1)
        self.assertTrue(done.is_set(), f"{op_name}: operation did not finish after lock release")
        self.assertEqual([], errors, f"{op_name}: raised {errors}")

    def test_persist_listener_mutations_block_while_listener_lock_is_held(self):
        persist = AutoQueuePersist()
        listener = MagicMock()
        persist.add_listener(listener)
        persist.add_pattern(AutoQueuePattern(pattern="seed"))
        lock = persist._AutoQueuePersist__listeners_lock

        cases = [
            ("add_pattern", lambda: persist.add_pattern(AutoQueuePattern(pattern="new"))),
            ("remove_pattern", lambda: persist.remove_pattern(AutoQueuePattern(pattern="seed"))),
            ("add_listener", lambda: persist.add_listener(MagicMock())),
        ]
        for op_name, op in cases:
            with self.subTest(op=op_name):
                self._assert_blocks_until_lock_released(lock, op, op_name)

    def test_patterns_returns_snapshot_copy(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="one"))

        snapshot = persist.patterns
        persist.add_pattern(AutoQueuePattern(pattern="two"))
        persist.remove_pattern(AutoQueuePattern(pattern="one"))

        self.assertEqual({AutoQueuePattern(pattern="one")}, snapshot)
        self.assertEqual({AutoQueuePattern(pattern="two")}, persist.patterns)

    def test_new_pattern_listener_mutations_block_while_listener_lock_is_held(self):
        listener = AutoQueuePersistListener()
        listener.pattern_added(AutoQueuePattern(pattern="seed"))
        lock = listener._AutoQueuePersistListener__lock

        cases = [
            ("pattern_added", lambda: listener.pattern_added(AutoQueuePattern(pattern="new"))),
            ("pattern_removed", lambda: listener.pattern_removed(AutoQueuePattern(pattern="seed"))),
            ("drain_new_patterns", listener.drain_new_patterns),
        ]
        for op_name, op in cases:
            with self.subTest(op=op_name):
                self._assert_blocks_until_lock_released(lock, op, op_name)

    def test_drain_new_patterns_returns_copy_and_clears(self):
        listener = AutoQueuePersistListener()
        listener.pattern_added(AutoQueuePattern(pattern="one"))

        drained = listener.drain_new_patterns()

        self.assertEqual({AutoQueuePattern(pattern="one")}, drained)
        listener.pattern_added(AutoQueuePattern(pattern="two"))
        listener.pattern_removed(AutoQueuePattern(pattern="one"))
        self.assertEqual({AutoQueuePattern(pattern="one")}, drained)
        self.assertEqual({AutoQueuePattern(pattern="two")}, listener.new_patterns)

    def test_drain_does_not_drop_pattern_added_after_drain(self):
        listener = AutoQueuePersistListener()
        listener.pattern_added(AutoQueuePattern(pattern="first"))

        self.assertEqual({AutoQueuePattern(pattern="first")}, listener.drain_new_patterns())

        # A pattern added after the drain must survive to the next cycle.
        listener.pattern_added(AutoQueuePattern(pattern="second"))
        self.assertEqual({AutoQueuePattern(pattern="second")}, listener.drain_new_patterns())


class TestAutoQueue(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(TestAutoQueue.__name__)
        handler = logging.StreamHandler(sys.stdout)
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)

        self.context = MagicMock()

        self.context.config = Config()
        self.context.config.autoqueue.enabled = True
        self.context.config.autoqueue.patterns_only = True
        self.context.config.autoqueue.auto_extract = True
        self.context.logger = self.logger
        self.context.breadcrumb_trace = MagicMock()
        self.controller = MagicMock()
        self.controller.get_model_files_and_add_listener = MagicMock()
        self.controller.queue_command = MagicMock()
        self.model_listener = None
        self.initial_model = []

        def get_model():
            return self.initial_model

        def get_model_and_capture_listener(listener: IModelListener):
            self.model_listener = listener
            return get_model()

        self.controller.get_model_files.side_effect = get_model
        self.controller.get_model_files_and_add_listener.side_effect = get_model_and_capture_listener
        self.controller.is_file_stopped.return_value = False

    def test_matching_new_files_are_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300

        self.model_listener.file_added(file_one)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)

        self.model_listener.file_added(file_two)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.Two", command.filename)

        self.model_listener.file_added(file_three)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.Three", command.filename)

        # All at once
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        auto_queue.process()
        calls = self.controller.queue_command.call_args_list[-3:]
        commands = [calls[i][0][0] for i in range(3)]
        self.assertEqual(set([Controller.Command.Action.QUEUE]*3), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three"}, {c.filename for c in commands})

    def test_process_records_auto_queue_breadcrumb_summary(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))

        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        self.model_listener.file_added(file_one)

        auto_queue.process()

        cycle_call = next(
            (
                call for call in self.context.breadcrumb_trace.record.call_args_list
                if len(call.args) >= 2 and call.args[1] == "auto_queue_cycle"
            ),
            None
        )
        self.assertIsNotNone(cycle_call)
        details = cycle_call.args[2]
        self.assertEqual(1, details["new_queue_candidates"])
        self.assertEqual(0, details["modified_queue_candidates"])
        self.assertEqual(1, details["queue_count"])
        self.assertEqual(0, details["extract_count"])
        self.assertEqual(True, details["patterns_only"])
        self.assertEqual(True, details["auto_extract_enabled"])
        self.assertEqual({}, details["queue_blocked_reason_counts"])
        self.assertEqual({"state_not_downloaded": 1}, details["extract_blocked_reason_counts"])
        self.assertEqual(1, len(details["blocked_samples"]))
        self.assertEqual("state_not_downloaded", details["blocked_samples"][0]["reason"])

    def test_process_records_blocked_reason_counts_for_stopped_and_pattern_mismatch(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="show*"))

        auto_queue = AutoQueue(self.context, persist, self.controller)

        stopped_file = ModelFile("show.s01e01.mkv", False)
        stopped_file.remote_size = 100
        missing_pattern = ModelFile("movie.mkv", False)
        missing_pattern.remote_size = 100
        extract_miss = ModelFile("archive.zip", False)
        extract_miss.state = ModelFile.State.DOWNLOADED
        extract_miss.local_size = 500
        extract_miss.remote_size = 500
        extract_miss.is_extractable = True

        self.controller.is_file_stopped.side_effect = lambda file_id: file_id == stopped_file.file_id

        self.model_listener.file_added(stopped_file)
        self.model_listener.file_added(missing_pattern)
        self.model_listener.file_added(extract_miss)

        auto_queue.process()

        cycle_call = next(
            (
                call for call in self.context.breadcrumb_trace.record.call_args_list
                if len(call.args) >= 2 and call.args[1] == "auto_queue_cycle"
            ),
            None
        )
        self.assertIsNotNone(cycle_call)
        details = cycle_call.args[2]
        self.assertEqual(
            {"explicitly_stopped": 1, "pattern_no_match": 1, "state_not_default": 1},
            details["queue_blocked_reason_counts"]
        )
        self.assertEqual({"state_not_downloaded": 2, "pattern_no_match": 1}, details["extract_blocked_reason_counts"])
        self.assertTrue(any(sample["reason"] == "explicitly_stopped" for sample in details["blocked_samples"]))

    def test_auto_queue_commands_include_flow_id_and_origin(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="show*"))

        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("show.s01e01.mkv", False)
        file_one.remote_size = 100

        self.model_listener.file_added(file_one)
        auto_queue.process()

        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual("auto_queue", command.origin)
        self.assertTrue(command.flow_id.startswith("autoq:1:QUEUE:"))

    def test_process_failure_restores_drained_new_patterns_for_next_cycle(self):
        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        self.initial_model = [file_one]

        auto_queue = AutoQueue(self.context, persist, self.controller)
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        self.controller.queue_command.side_effect = RuntimeError("queue boom")

        with self.assertRaises(RuntimeError):
            auto_queue.process()

        self.assertEqual(
            {AutoQueuePattern(pattern="File.One")},
            auto_queue._AutoQueue__persist_listener.new_patterns,
        )

        self.controller.queue_command.side_effect = None
        self.controller.queue_command.reset_mock()

        auto_queue.process()

        self.assertEqual(1, self.controller.queue_command.call_count)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)

    def test_matching_initial_files_are_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300
        file_four = ModelFile("File.Four", True)
        file_four.remote_size = 400
        file_five = ModelFile("File.Five", True)
        file_five.remote_size = 500

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()

        calls = self.controller.queue_command.call_args_list
        self.assertEqual(3, len(calls))
        commands = [calls[i][0][0] for i in range(3)]
        self.assertEqual(set([Controller.Command.Action.QUEUE]*3), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three"}, {c.filename for c in commands})

    def test_non_matches(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("Two", True)
        file_one.remote_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_matching_is_case_insensitive(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="FiLe.oNe"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("FiLe.oNe", True)
        file_one.remote_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("FiLe.oNe", command.filename)

    def test_partial_matches(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="file"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("fileone", True)  # at start
        file_one.remote_size = 100
        file_two = ModelFile("twofile", True)  # at end
        file_two.remote_size = 100
        file_three = ModelFile("onefiletwo", True)  # in middle
        file_three.remote_size = 100
        file_four = ModelFile("fionele", True)  # no match
        file_four.remote_size = 100
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        self.model_listener.file_added(file_four)
        auto_queue.process()
        self.assertEqual(3, self.controller.queue_command.call_count)
        commands = [call[0][0] for call in self.controller.queue_command.call_args_list]
        commands_dict = {command.filename: command for command in commands}
        self.assertTrue("fileone" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["fileone"].action)
        self.assertTrue("twofile" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["twofile"].action)
        self.assertTrue("onefiletwo" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["onefiletwo"].action)

    def test_wildcard_at_start_matches(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="*.mkv"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One.mkv", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two.jpg", True)
        file_two.remote_size = 100
        file_three = ModelFile(".mkvFile.Three", True)
        file_three.remote_size = 100
        file_four = ModelFile("FileFour.mkv", True)
        file_four.remote_size = 100
        file_five = ModelFile("FileFive.mkv.more", True)
        file_five.remote_size = 100
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        self.model_listener.file_added(file_four)
        self.model_listener.file_added(file_five)
        auto_queue.process()
        self.assertEqual(2, self.controller.queue_command.call_count)
        commands = [call[0][0] for call in self.controller.queue_command.call_args_list]
        commands_dict = {command.filename: command for command in commands}
        self.assertTrue("File.One.mkv" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["File.One.mkv"].action)
        self.assertTrue("FileFour.mkv" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["FileFour.mkv"].action)

    def test_wildcard_at_end_matches(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File*"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One.mkv", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two.jpg", True)
        file_two.remote_size = 100
        file_three = ModelFile(".mkvFile.Three", True)
        file_three.remote_size = 100
        file_four = ModelFile("FileFour.mkv", True)
        file_four.remote_size = 100
        file_five = ModelFile("FileFive.mkv.more", True)
        file_five.remote_size = 100
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        self.model_listener.file_added(file_four)
        self.model_listener.file_added(file_five)
        auto_queue.process()
        self.assertEqual(4, self.controller.queue_command.call_count)
        commands = [call[0][0] for call in self.controller.queue_command.call_args_list]
        commands_dict = {command.filename: command for command in commands}
        self.assertTrue("File.One.mkv" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["File.One.mkv"].action)
        self.assertTrue("File.Two.jpg" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["File.Two.jpg"].action)
        self.assertTrue("FileFour.mkv" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["FileFour.mkv"].action)
        self.assertTrue("FileFive.mkv.more" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["FileFive.mkv.more"].action)

    def test_wildcard_in_middle_matches(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="*mkv*"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One.mkv", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two.jpg", True)
        file_two.remote_size = 100
        file_three = ModelFile(".mkvFile.Three", True)
        file_three.remote_size = 100
        file_four = ModelFile("FileFour.mkv", True)
        file_four.remote_size = 100
        file_five = ModelFile("FileFive.mkv.more", True)
        file_five.remote_size = 100
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        self.model_listener.file_added(file_four)
        self.model_listener.file_added(file_five)
        auto_queue.process()
        self.assertEqual(4, self.controller.queue_command.call_count)
        commands = [call[0][0] for call in self.controller.queue_command.call_args_list]
        commands_dict = {command.filename: command for command in commands}
        self.assertTrue("File.One.mkv" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["File.One.mkv"].action)
        self.assertTrue(".mkvFile.Three" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict[".mkvFile.Three"].action)
        self.assertTrue("FileFour.mkv" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["FileFour.mkv"].action)
        self.assertTrue("FileFive.mkv.more" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["FileFive.mkv.more"].action)

    def test_wildcard_matches_are_case_insensitive(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="*.mkv"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One.mKV", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two.jpg", True)
        file_two.remote_size = 100
        file_three = ModelFile(".mkvFile.Three", True)
        file_three.remote_size = 100
        file_four = ModelFile("FileFour.MKV", True)
        file_four.remote_size = 100
        file_five = ModelFile("FileFive.mkv.more", True)
        file_five.remote_size = 100
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        self.model_listener.file_added(file_four)
        self.model_listener.file_added(file_five)
        auto_queue.process()
        self.assertEqual(2, self.controller.queue_command.call_count)
        commands = [call[0][0] for call in self.controller.queue_command.call_args_list]
        commands_dict = {command.filename: command for command in commands}
        self.assertTrue("File.One.mKV" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["File.One.mKV"].action)
        self.assertTrue("FileFour.MKV" in commands_dict)
        self.assertEqual(Controller.Command.Action.QUEUE, commands_dict["FileFour.MKV"].action)

    def test_matching_local_files_are_not_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = None
        file_one.local_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_matching_deleted_files_are_not_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_one.local_size = None
        file_one.state = ModelFile.State.DELETED
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_matching_downloading_files_are_not_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_one.local_size = 0
        file_one.state = ModelFile.State.DOWNLOADING
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()
        file_one_new = ModelFile("File.One", True)
        file_one_new.remote_size = 100
        file_one_new.local_size = 50
        file_one_new.state = ModelFile.State.DOWNLOADING
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_matching_queued_files_are_not_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_one.state = ModelFile.State.QUEUED
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_matching_downloaded_files_are_not_queued(self):
        # Disable auto-extract
        self.context.config.autoqueue.auto_extract = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_auto_queued_file_not_re_queued_after_stopping(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)

        file_one_updated = ModelFile("File.One", True)
        file_one_updated.remote_size = 100
        file_one_updated.local_size = 50
        self.model_listener.file_updated(file_one, file_one_updated)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)

    def test_partial_file_is_not_auto_queued_after_remote_discovery(self):
        # Test that a partial local file is not auto-queued when discovered on remote some time later
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        # Local discovery
        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        # Remote discovery
        file_one_new = ModelFile("File.One", True)
        file_one_new.local_size = 100
        file_one_new.remote_size = 200
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_partial_file_is_auto_queued_after_actual_remote_update(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.remote_size = 150
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        file_one_new = ModelFile("File.One", True)
        file_one_new.local_size = 100
        file_one_new.remote_size = 200
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)

    def test_duplicate_names_from_different_path_pairs_are_queued_separately(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="Release"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("Release", True)
        file_one.remote_size = 100
        file_one.path_pair_id = "tv"

        file_two = ModelFile("Release", True)
        file_two.remote_size = 100
        file_two.path_pair_id = "movies"

        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        auto_queue.process()

        calls = self.controller.queue_command.call_args_list
        self.assertEqual(2, len(calls))
        commands = [calls[i][0][0] for i in range(2)]
        self.assertEqual(set([Controller.Command.Action.QUEUE] * 2), {c.action for c in commands})
        self.assertEqual({file_one.file_id, file_two.file_id}, {c.filename for c in commands})

    def test_explicitly_stopped_file_is_not_auto_queued(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="Release"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        stopped_file = ModelFile("Release", True)
        stopped_file.remote_size = 100
        stopped_file.path_pair_id = "tv"

        queued_file = ModelFile("Release", True)
        queued_file.remote_size = 100
        queued_file.path_pair_id = "movies"

        self.controller.is_file_stopped.side_effect = lambda file_id: file_id == stopped_file.file_id

        self.model_listener.file_added(stopped_file)
        self.model_listener.file_added(queued_file)
        auto_queue.process()

        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual(queued_file.file_id, command.filename)

    def test_new_matching_pattern_queues_existing_files(self):
        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300
        file_four = ModelFile("File.Four", True)
        file_four.remote_size = 400
        file_five = ModelFile("File.Five", True)
        file_five.remote_size = 500

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.Two", command.filename)
        self.controller.queue_command.reset_mock()

        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.Three", command.filename)
        self.controller.queue_command.reset_mock()

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_new_matching_pattern_doesnt_queue_local_file(self):
        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100

        self.initial_model = [file_one]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_removed_pattern_doesnt_queue_new_file(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="One"))
        persist.add_pattern(AutoQueuePattern(pattern="Two"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        self.model_listener.file_added(file_one)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        persist.remove_pattern(AutoQueuePattern(pattern="Two"))

        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 100
        self.model_listener.file_added(file_two)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_adding_then_removing_pattern_doesnt_queue_existing_file(self):
        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300
        file_four = ModelFile("File.Four", True)
        file_four.remote_size = 400
        file_five = ModelFile("File.Five", True)
        file_five.remote_size = 500

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.remove_pattern(AutoQueuePattern(pattern="File.One"))
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_downloaded_file_with_changed_remote_size_is_queued(self):
        # Disable auto-extract
        self.context.config.autoqueue.auto_extract = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        file_one_updated = ModelFile("File.One", True)
        file_one_updated.remote_size = 200
        file_one_updated.local_size = 100
        file_one_updated.state = ModelFile.State.DEFAULT
        self.model_listener.file_updated(file_one, file_one_updated)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)

    def test_no_files_are_queued_when_disabled(self):
        self.context.config.autoqueue.enabled = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300
        file_four = ModelFile("File.Four", True)
        file_four.remote_size = 400
        file_five = ModelFile("File.Five", True)
        file_five.remote_size = 500

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # First with patterns_only ON
        self.context.config.autoqueue.patterns_only = True
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        # Second with patterns_only OFF
        self.context.config.autoqueue.patterns_only = False
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_all_files_are_queued_when_patterns_only_disabled(self):
        self.context.config.autoqueue.patterns_only = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300
        file_four = ModelFile("File.Four", True)
        file_four.remote_size = 400
        file_five = ModelFile("File.Five", True)
        file_five.remote_size = 500

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        calls = self.controller.queue_command.call_args_list
        self.assertEqual(5, len(calls))
        commands = [calls[i][0][0] for i in range(5)]
        self.assertEqual(set([Controller.Command.Action.QUEUE]*5), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three", "File.Four", "File.Five"},
                         {c.filename for c in commands})

    def test_all_files_are_queued_when_patterns_only_disabled_and_no_patterns_exist(self):
        self.context.config.autoqueue.patterns_only = False

        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_two = ModelFile("File.Two", True)
        file_two.remote_size = 200
        file_three = ModelFile("File.Three", True)
        file_three.remote_size = 300
        file_four = ModelFile("File.Four", True)
        file_four.remote_size = 400
        file_five = ModelFile("File.Five", True)
        file_five.remote_size = 500

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        calls = self.controller.queue_command.call_args_list
        self.assertEqual(5, len(calls))
        commands = [calls[i][0][0] for i in range(5)]
        self.assertEqual(set([Controller.Command.Action.QUEUE]*5), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three", "File.Four", "File.Five"},
                         {c.filename for c in commands})

    def test_matching_new_files_are_extracted(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("File.One", True)
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.local_size = 100
        file_one.is_extractable = True
        file_two = ModelFile("File.Two", True)
        file_two.state = ModelFile.State.DOWNLOADED
        file_two.local_size = 200
        file_two.is_extractable = True
        file_three = ModelFile("File.Three", True)
        file_three.state = ModelFile.State.DOWNLOADED
        file_three.local_size = 300
        file_three.is_extractable = True

        self.model_listener.file_added(file_one)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.One", command.filename)

        self.model_listener.file_added(file_two)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.Two", command.filename)

        self.model_listener.file_added(file_three)
        auto_queue.process()
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.Three", command.filename)

        # All at once
        self.model_listener.file_added(file_one)
        self.model_listener.file_added(file_two)
        self.model_listener.file_added(file_three)
        auto_queue.process()
        calls = self.controller.queue_command.call_args_list[-3:]
        commands = [calls[i][0][0] for i in range(3)]
        self.assertEqual(set([Controller.Command.Action.EXTRACT]*3), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three"}, {c.filename for c in commands})

    def test_matching_initial_files_are_extracted(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.local_size = 100
        file_one.is_extractable = True
        file_two = ModelFile("File.Two", True)
        file_two.state = ModelFile.State.DOWNLOADED
        file_two.local_size = 200
        file_two.is_extractable = True
        file_three = ModelFile("File.Three", True)
        file_three.state = ModelFile.State.DOWNLOADED
        file_three.local_size = 300
        file_three.is_extractable = True
        file_four = ModelFile("File.Four", True)
        file_four.state = ModelFile.State.DOWNLOADED
        file_four.local_size = 400
        file_four.is_extractable = True
        file_five = ModelFile("File.Five", True)
        file_five.state = ModelFile.State.DOWNLOADED
        file_five.local_size = 500
        file_five.is_extractable = True

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()

        calls = self.controller.queue_command.call_args_list
        self.assertEqual(3, len(calls))
        commands = [calls[i][0][0] for i in range(3)]
        self.assertEqual(set([Controller.Command.Action.EXTRACT]*3), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three"}, {c.filename for c in commands})

    def test_downloaded_extractable_files_are_extracted_before_remote_deletion(self):
        self.context.config.autoqueue.auto_delete_remote = True

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        file_one = ModelFile("archive.zip", False)
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.local_size = 100
        file_one.remote_size = 100
        file_one.is_extractable = True

        self.model_listener.file_added(file_one)
        auto_queue.process()

        calls = self.controller.queue_command.call_args_list
        self.assertEqual(1, len(calls))
        command = calls[0][0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("archive.zip", command.filename)

    def test_extracted_completion_transition_deletes_remote_when_enabled(self):
        self.context.config.autoqueue.auto_delete_remote = True

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        old_file = ModelFile("archive.zip", False)
        old_file.state = ModelFile.State.EXTRACTING
        old_file.local_size = 100
        old_file.remote_size = 100
        old_file.is_extractable = True

        new_file = ModelFile("archive.zip", False)
        new_file.state = ModelFile.State.EXTRACTED
        new_file.local_size = 100
        new_file.remote_size = 100
        new_file.is_extractable = True

        self.model_listener.file_updated(old_file, new_file)
        auto_queue.process()

        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("archive.zip", command.filename)

    def test_marker_style_downloaded_to_extracted_transition_does_not_delete_remote(self):
        self.context.config.autoqueue.auto_delete_remote = True

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        old_file = ModelFile("archive.zip", False)
        old_file.state = ModelFile.State.DOWNLOADED
        old_file.local_size = 100
        old_file.remote_size = 100
        old_file.is_extractable = True

        new_file = ModelFile("archive.zip", False)
        new_file.state = ModelFile.State.EXTRACTED
        new_file.local_size = 100
        new_file.remote_size = 100
        new_file.is_extractable = True

        self.model_listener.file_updated(old_file, new_file)
        auto_queue.process()

        self.controller.queue_command.assert_not_called()

    def test_non_extract_download_completion_deletes_remote_when_enabled(self):
        self.context.config.autoqueue.auto_delete_remote = True

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        old_file = ModelFile("archive.zip", False)
        old_file.state = ModelFile.State.DEFAULT
        old_file.remote_size = None

        new_file = ModelFile("archive.zip", False)
        new_file.state = ModelFile.State.DOWNLOADED
        new_file.local_size = 100
        new_file.remote_size = 100
        new_file.is_extractable = False

        self.model_listener.file_updated(old_file, new_file)
        auto_queue.process()

        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.DELETE_REMOTE, command.action)
        self.assertEqual("archive.zip", command.filename)

    def test_disabled_auto_delete_does_not_queue_remote_delete(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        old_file = ModelFile("archive.zip", False)
        old_file.state = ModelFile.State.DEFAULT

        new_file = ModelFile("archive.zip", False)
        new_file.state = ModelFile.State.DOWNLOADED
        new_file.local_size = 100
        new_file.remote_size = 100
        new_file.is_extractable = False

        self.model_listener.file_updated(old_file, new_file)
        auto_queue.process()

        self.controller.queue_command.assert_not_called()

    def test_remote_size_none_suppresses_remote_delete(self):
        self.context.config.autoqueue.auto_delete_remote = True

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        old_file = ModelFile("archive.zip", False)
        old_file.state = ModelFile.State.DEFAULT

        new_file = ModelFile("archive.zip", False)
        new_file.state = ModelFile.State.DOWNLOADED
        new_file.local_size = 100
        new_file.remote_size = None
        new_file.is_extractable = False

        self.model_listener.file_updated(old_file, new_file)
        auto_queue.process()

        self.controller.queue_command.assert_not_called()

    def test_initial_completed_model_files_do_not_auto_delete_remote(self):
        self.context.config.autoqueue.auto_delete_remote = True

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        file_one = ModelFile("archive.zip", False)
        file_one.state = ModelFile.State.EXTRACTED
        file_one.local_size = 100
        file_one.remote_size = 100
        file_one.is_extractable = True
        self.initial_model = [file_one]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()

        self.controller.queue_command.assert_not_called()

    def test_new_pattern_backfilled_completed_file_does_not_auto_delete_remote(self):
        self.context.config.autoqueue.auto_delete_remote = True
        self.context.config.autoqueue.patterns_only = True

        persist = AutoQueuePersist()

        file_one = ModelFile("archive.zip", False)
        file_one.state = ModelFile.State.EXTRACTED
        file_one.local_size = 100
        file_one.remote_size = 100
        file_one.is_extractable = True
        self.initial_model = [file_one]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        auto_queue.process()

        self.controller.queue_command.assert_not_called()

    def test_auto_extract_trace_logs_queued_decision_for_selected_file(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="archive.zip"))

        file_one = ModelFile("archive.zip", False)
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.local_size = 100
        file_one.is_extractable = True
        self.initial_model = [file_one]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue._AutoQueue__target_archive_trace_file_id = file_one.file_id
        trace_logger = auto_queue._AutoQueue__target_archive_trace_logger

        with patch.object(trace_logger, "info") as trace_info:
            auto_queue.process()

        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("auto_extract_decision", payload["event"])
        self.assertEqual("queued", payload["decision"])
        self.assertEqual(file_one.file_id, payload["file"]["file_id"])
        self.assertEqual("archive.zip", payload["file"]["name"])

    def test_auto_extract_trace_logs_null_pattern_for_selected_candidate(self):
        self.context.config.autoqueue.patterns_only = False

        persist = AutoQueuePersist()

        file_one = ModelFile("archive.zip", False)
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.local_size = 100
        file_one.is_extractable = True
        self.initial_model = [file_one]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue._AutoQueue__target_archive_trace_file_id = file_one.file_id
        trace_logger = auto_queue._AutoQueue__target_archive_trace_logger

        with patch.object(trace_logger, "info") as trace_info:
            auto_queue.process()

        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        self.assertEqual(1, trace_info.call_count)
        payload = json.loads(trace_info.call_args[0][1])
        self.assertEqual("auto_extract_decision", payload["event"])
        self.assertEqual("queued", payload["decision"])
        self.assertIsNone(payload["pattern"])
        self.assertEqual(file_one.file_id, payload["file"]["file_id"])

    def test_patterns_only_auto_extract_does_not_clear_startup_new_file_marker(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="different-pattern"))

        file_one = ModelFile("archive.zip", False)
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.local_size = 100
        file_one.is_extractable = True
        self.initial_model = [file_one]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()

        self.controller.queue_command.assert_not_called()
        self.controller.clear_extracted_marker.assert_not_called()

    def test_patterns_only_auto_extract_clears_stale_extracted_marker_for_modified_candidate(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="different-pattern"))

        old_file = ModelFile("archive.zip", False)
        old_file.state = ModelFile.State.DEFAULT
        old_file.local_size = 50
        old_file.is_extractable = True
        new_file = ModelFile("archive.zip", False)
        new_file.state = ModelFile.State.DOWNLOADED
        new_file.local_size = 100
        new_file.is_extractable = True
        self.initial_model = []

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        self.model_listener.file_updated(old_file, new_file)

        auto_queue.process()

        self.controller.queue_command.assert_not_called()
        self.controller.clear_extracted_marker.assert_called_once_with(new_file)

    def test_new_matching_pattern_extracts_existing_files(self):
        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.is_extractable = True
        file_two = ModelFile("File.Two", True)
        file_two.local_size = 200
        file_two.state = ModelFile.State.DOWNLOADED
        file_two.is_extractable = True
        file_three = ModelFile("File.Three", True)
        file_three.local_size = 300
        file_three.state = ModelFile.State.DOWNLOADED
        file_three.is_extractable = True
        file_four = ModelFile("File.Four", True)
        file_four.local_size = 400
        file_four.state = ModelFile.State.DOWNLOADED
        file_four.is_extractable = True
        file_five = ModelFile("File.Five", True)
        file_five.local_size = 500
        file_five.state = ModelFile.State.DOWNLOADED
        file_five.is_extractable = True

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.Two", command.filename)
        self.controller.queue_command.reset_mock()

        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.Three", command.filename)
        self.controller.queue_command.reset_mock()

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_non_extractable_files_are_not_extracted(self):
        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.is_extractable = True
        file_two = ModelFile("File.Two", True)
        file_two.local_size = 200
        file_two.state = ModelFile.State.DOWNLOADED
        file_two.is_extractable = False

        self.initial_model = [file_one, file_two]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_no_files_are_extracted_when_disabled(self):
        self.context.config.autoqueue.enabled = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_two = ModelFile("File.Two", True)
        file_two.local_size = 200
        file_two.state = ModelFile.State.DOWNLOADED
        file_three = ModelFile("File.Three", True)
        file_three.local_size = 300
        file_three.state = ModelFile.State.DOWNLOADED
        file_four = ModelFile("File.Four", True)
        file_four.local_size = 400
        file_four.state = ModelFile.State.DOWNLOADED
        file_five = ModelFile("File.Five", True)
        file_five.local_size = 500
        file_five.state = ModelFile.State.DOWNLOADED

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # First with patterns_only ON
        self.context.config.autoqueue.patterns_only = True
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        # Second with patterns_only OFF
        self.context.config.autoqueue.patterns_only = False
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_no_files_are_extracted_when_auto_extract_disabled(self):
        self.context.config.autoqueue.enabled = True
        self.context.config.autoqueue.auto_extract = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_two = ModelFile("File.Two", True)
        file_two.local_size = 200
        file_two.state = ModelFile.State.DOWNLOADED
        file_three = ModelFile("File.Three", True)
        file_three.local_size = 300
        file_three.state = ModelFile.State.DOWNLOADED
        file_four = ModelFile("File.Four", True)
        file_four.local_size = 400
        file_four.state = ModelFile.State.DOWNLOADED
        file_five = ModelFile("File.Five", True)
        file_five.local_size = 500
        file_five.state = ModelFile.State.DOWNLOADED

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # First with patterns_only ON
        self.context.config.autoqueue.patterns_only = True
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        # Second with patterns_only OFF
        self.context.config.autoqueue.patterns_only = False
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_all_files_are_extracted_when_patterns_only_disabled(self):
        self.context.config.autoqueue.patterns_only = False

        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Two"))
        persist.add_pattern(AutoQueuePattern(pattern="File.Three"))

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.is_extractable = True
        file_two = ModelFile("File.Two", True)
        file_two.local_size = 200
        file_two.state = ModelFile.State.DOWNLOADED
        file_two.is_extractable = True
        file_three = ModelFile("File.Three", True)
        file_three.local_size = 300
        file_three.state = ModelFile.State.DOWNLOADED
        file_three.is_extractable = True
        file_four = ModelFile("File.Four", True)
        file_four.local_size = 400
        file_four.state = ModelFile.State.DOWNLOADED
        file_four.is_extractable = True
        file_five = ModelFile("File.Five", True)
        file_five.local_size = 500
        file_five.state = ModelFile.State.DOWNLOADED
        file_five.is_extractable = True

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        calls = self.controller.queue_command.call_args_list
        self.assertEqual(5, len(calls))
        commands = [calls[i][0][0] for i in range(5)]
        self.assertEqual(set([Controller.Command.Action.EXTRACT]*5), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three", "File.Four", "File.Five"},
                         {c.filename for c in commands})

    def test_all_files_are_extracted_when_patterns_only_disabled_and_no_patterns_exist(self):
        self.context.config.autoqueue.patterns_only = False

        persist = AutoQueuePersist()

        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.is_extractable = True
        file_two = ModelFile("File.Two", True)
        file_two.local_size = 200
        file_two.state = ModelFile.State.DOWNLOADED
        file_two.is_extractable = True
        file_three = ModelFile("File.Three", True)
        file_three.local_size = 300
        file_three.state = ModelFile.State.DOWNLOADED
        file_three.is_extractable = True
        file_four = ModelFile("File.Four", True)
        file_four.local_size = 400
        file_four.state = ModelFile.State.DOWNLOADED
        file_four.is_extractable = True
        file_five = ModelFile("File.Five", True)
        file_five.local_size = 500
        file_five.state = ModelFile.State.DOWNLOADED
        file_five.is_extractable = True

        self.initial_model = [file_one, file_two, file_three, file_four, file_five]

        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)
        auto_queue.process()
        calls = self.controller.queue_command.call_args_list
        self.assertEqual(5, len(calls))
        commands = [calls[i][0][0] for i in range(5)]
        self.assertEqual(set([Controller.Command.Action.EXTRACT]*5), {c.action for c in commands})
        self.assertEqual({"File.One", "File.Two", "File.Three", "File.Four", "File.Five"},
                         {c.filename for c in commands})

    def test_file_is_extracted_after_finishing_download(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        # File exists remotely and is auto-queued
        file_one = ModelFile("File.One", True)
        file_one.remote_size = 100
        file_one.is_extractable = True
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.QUEUE, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        # File starts downloading
        file_one_new = ModelFile("File.One", True)
        file_one_new.remote_size = 100
        file_one_new.local_size = 50
        file_one_new.state = ModelFile.State.DOWNLOADING
        file_one_new.is_extractable = True
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        # File finishes downloading
        file_one = file_one_new
        file_one_new = ModelFile("File.One", True)
        file_one_new.remote_size = 100
        file_one_new.local_size = 100
        file_one_new.state = ModelFile.State.DOWNLOADED
        file_one_new.is_extractable = True
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.One", command.filename)

    def test_downloaded_file_is_NOT_re_extracted_after_modified(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        # File is auto-extracted
        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.is_extractable = True
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        # File is modified
        file_one_new = ModelFile("File.One", True)
        file_one_new.local_size = 101
        file_one_new.state = ModelFile.State.DOWNLOADED
        file_one_new.is_extractable = True
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

    def test_downloaded_file_is_NOT_re_extracted_after_failed_extraction(self):
        persist = AutoQueuePersist()
        persist.add_pattern(AutoQueuePattern(pattern="File.One"))
        # noinspection PyTypeChecker
        auto_queue = AutoQueue(self.context, persist, self.controller)

        # File is auto-extracted
        file_one = ModelFile("File.One", True)
        file_one.local_size = 100
        file_one.state = ModelFile.State.DOWNLOADED
        file_one.is_extractable = True
        self.model_listener.file_added(file_one)
        auto_queue.process()
        self.controller.queue_command.assert_called_once_with(unittest.mock.ANY)
        command = self.controller.queue_command.call_args[0][0]
        self.assertEqual(Controller.Command.Action.EXTRACT, command.action)
        self.assertEqual("File.One", command.filename)
        self.controller.queue_command.reset_mock()

        # File is extracting
        file_one_new = ModelFile("File.One", True)
        file_one_new.local_size = 101
        file_one_new.state = ModelFile.State.EXTRACTING
        file_one_new.is_extractable = True
        self.model_listener.file_updated(file_one, file_one_new)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()

        # Extraction fails and file goes back to DOWNLOADED
        file_one_newer = ModelFile("File.One", True)
        file_one_newer.local_size = 101
        file_one_newer.state = ModelFile.State.DOWNLOADED
        file_one_newer.is_extractable = True
        self.model_listener.file_updated(file_one_new, file_one_newer)
        auto_queue.process()
        self.controller.queue_command.assert_not_called()
