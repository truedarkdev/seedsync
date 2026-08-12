# Copyright 2026, SeedSync Contributors, All rights reserved.

import threading
import unittest
import math
from unittest.mock import patch

import common.performance_diagnostics as performance_diagnostics

from common.performance_diagnostics import (
    DURATION_CONTROLLER_PROCESS,
    DURATION_MODEL_BUILDER_SET_ACTIVE_FILES,
    DURATION_MODEL_BUILDER_SET_LFTP_STATUSES,
    DURATION_MODEL_BUILDER_SET_LOCAL_FILES,
    DURATION_MODEL_BUILDER_SET_REMOTE_FILES,
    DURATION_MODEL_BUILDER_SET_STOPPED_FILES,
    DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
    DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION,
    DURATION_REMOTE_SCAN_STREAM_PARSING,
    DURATION_MODEL_UPDATE_BUILD_FINALIZATION,
    DURATION_MODEL_UPDATE_BUILDER_SYNC,
    DURATION_MODEL_UPDATE_LIFECYCLE_MAINTENANCE,
    DURATION_MODEL_UPDATE_SCAN_INTAKE,
    DURATION_MODEL_UPDATE_STATE_PREPARATION,
    DURATION_MODEL_UPDATE_STATUS_INGESTION,
    MODEL_REBUILD_REASON_COLLISION_RETRY,
    FixedDurationRecorder,
    PerformanceDiagnosticsCollector,
    ProcessContainerSampler,
)


class _Sampler:
    def __init__(self):
        self.calls = 0

    def sample(self):
        self.calls += 1
        return {"process_rss_bytes": self.calls * 10, "ignored_path": "/private"}


class _FailingSampler:
    def sample(self):
        raise RuntimeError("diagnostic failure")


class TestPerformanceDiagnosticsCollector(unittest.TestCase):
    def test_rebuild_reason_counters_are_fixed_and_bounded(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        collector.increment("model_rebuild_collision_retry", 2)
        collector.increment("progressive_delta_publications", 2)
        collector.increment("progressive_delta_root_visits", 3)
        collector.increment("scan_priority_requests", 2)
        collector.increment("scan_priority_interrupts")
        collector.increment("scan_priority_targeted_runs", 2)
        collector.increment("scan_priority_full_followups")
        collector.increment("model_rebuild_collision_retry", -1)
        collector.increment("model_rebuild:/private/path")
        snapshot = collector.snapshot()
        self.assertEqual(2, snapshot["counters"]["model_rebuild_collision_retry"])
        self.assertEqual(2, snapshot["counters"]["progressive_delta_publications"])
        self.assertEqual(3, snapshot["counters"]["progressive_delta_root_visits"])
        self.assertEqual(2, snapshot["counters"]["scan_priority_requests"])
        self.assertEqual(1, snapshot["counters"]["scan_priority_interrupts"])
        self.assertEqual(2, snapshot["counters"]["scan_priority_targeted_runs"])
        self.assertEqual(1, snapshot["counters"]["scan_priority_full_followups"])
        self.assertNotIn("model_rebuild:/private/path", snapshot["counters"])
        self.assertEqual("collision_retry", MODEL_REBUILD_REASON_COLLISION_RETRY)

    def test_samples_are_bounded_numeric_and_track_peaks(self):
        collector = PerformanceDiagnosticsCollector(lambda: True, retention_depth=2)
        collector.record_sample({"process_rss_bytes": 10, "process_fds": 3, "path": "/secret"})
        collector.record_sample({"process_rss_bytes": 30})
        collector.record_sample({"process_rss_bytes": 20})
        result = collector.snapshot()
        self.assertEqual(2, result["sample_count"])
        self.assertEqual(1, result["counters"]["samples_dropped"])
        self.assertEqual(30, result["peaks"]["process_rss_bytes"])
        self.assertNotIn("path", result["samples"][0])
        self.assertNotIn("/secret", str(result))

    def test_concurrent_samples_and_reset_keep_a_valid_snapshot(self):
        collector = PerformanceDiagnosticsCollector(lambda: True, retention_depth=64)
        threads = [threading.Thread(target=lambda: [collector.record_sample({"process_fds": 1}) for _ in range(30)])
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        collector.reset()
        snapshot = collector.snapshot()
        self.assertEqual(0, snapshot["sample_count"])
        self.assertEqual(2, snapshot["session"])
        self.assertEqual(120, snapshot["session_start_sequence"])
        self.assertTrue(snapshot["enabled"])

    def test_sampler_interval_failure_isolation_and_duration_gate(self):
        clock = [0.0]
        sampler = _Sampler()
        enabled = [True]
        collector = PerformanceDiagnosticsCollector(lambda: enabled[0], sampler=sampler, monotonic_fn=lambda: clock[0])
        self.assertTrue(collector.sample_if_due())
        disabled = PerformanceDiagnosticsCollector(lambda: False, sampler=sampler)
        self.assertFalse(disabled.sample_if_due())
        self.assertEqual(1, sampler.calls)
        self.assertFalse(collector.sample_if_due())
        clock[0] = 5.0
        self.assertTrue(collector.sample_if_due())
        failing = PerformanceDiagnosticsCollector(lambda: True, sampler=_FailingSampler())
        self.assertFalse(failing.sample_if_due())
        self.assertEqual(1, failing.snapshot()["counters"]["sampler_failures"])
        enabled[0] = False
        collector.observe_duration(DURATION_CONTROLLER_PROCESS, 2.0)
        self.assertEqual({}, collector.snapshot()["durations"])
        enabled[0] = True
        collector = PerformanceDiagnosticsCollector(lambda: enabled[0])
        collector.observe_duration(DURATION_CONTROLLER_PROCESS, 2.0)
        collector.observe_duration("/secret-path", 3.0)
        duration = collector.snapshot()["durations"][DURATION_CONTROLLER_PROCESS]
        self.assertEqual(1, duration["count"])
        self.assertEqual(2.0, duration["average_wall_seconds"])

    def test_child_duration_aggregate_is_fixed_and_validated(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        generation = collector.duration_generation()
        collector.observe_duration_aggregate(
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
            {
                "count": 2,
                "total_wall_seconds": 3.0,
                "max_wall_seconds": 2.0,
                "total_cpu_seconds": 1.0,
                "cpu_observation_count": 2,
            },
            expected_generation=generation,
        )
        collector.observe_duration_aggregate(
            "/private/path",
            {"count": 4, "total_wall_seconds": 4.0, "max_wall_seconds": 1.0, "cpu_observation_count": 0},
        )
        collector.observe_duration_aggregate(
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
            {"count": 1, "total_wall_seconds": 1.0, "max_wall_seconds": 2.0, "cpu_observation_count": 0},
            expected_generation=generation - 1,
        )
        duration = collector.snapshot()["durations"][DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL]
        self.assertEqual(2, duration["count"])
        self.assertEqual(3.0, duration["total_wall_seconds"])
        self.assertNotIn("/private/path", collector.snapshot()["durations"])

    def test_child_recorder_collects_only_fixed_metrics_and_can_be_disabled(self):
        clock = [1.0]
        cpu_clock = [2.0]
        recorder = FixedDurationRecorder(
            True, monotonic_fn=lambda: clock[0], thread_time_fn=lambda: cpu_clock[0]
        )
        token = recorder.begin_duration(DURATION_REMOTE_SCAN_STREAM_PARSING)
        clock[0], cpu_clock[0] = 3.0, 2.5
        recorder.finish_duration(DURATION_REMOTE_SCAN_STREAM_PARSING, token)
        recorder.finish_duration("/private/path", token)
        snapshot = recorder.snapshot()
        self.assertEqual(1, snapshot[DURATION_REMOTE_SCAN_STREAM_PARSING]["count"])
        self.assertNotIn("/private/path", snapshot)
        disabled = FixedDurationRecorder(False)
        self.assertIsNone(disabled.begin_duration(DURATION_REMOTE_SCAN_STREAM_PARSING))
        self.assertEqual({}, disabled.snapshot())

    def test_export_is_bounded_and_reset_does_not_disable_collection(self):
        collector = PerformanceDiagnosticsCollector(lambda: True, retention_depth=2)
        collector.record_sample({"process_cpu_percent_one_core": 5.0})
        exported = collector.export_snapshot()
        self.assertEqual("seedsync.performance-diagnostics.v1", exported["schema"])
        self.assertIn("export_max_bytes", exported)
        collector.reset()
        collector.record_sample({"process_cpu_percent_one_core": 9.0})
        self.assertTrue(collector.snapshot()["enabled"])
        self.assertEqual(1, collector.snapshot()["sample_count"])

    def test_disabled_direct_records_are_not_retained_and_existing_history_is_preserved(self):
        enabled = [True]
        collector = PerformanceDiagnosticsCollector(lambda: enabled[0])
        collector.record_sample({"process_rss_bytes": 10})
        enabled[0] = False
        collector.record_sample({"process_rss_bytes": 99})
        snapshot = collector.snapshot()
        self.assertEqual(1, snapshot["sample_count"])
        self.assertEqual(10, snapshot["samples"][0]["process_rss_bytes"])

    def test_due_samples_close_independent_stage_windows_and_preserve_peaks(self):
        clock = [0.0]
        thread_clock = [0.0]
        collector = PerformanceDiagnosticsCollector(
            lambda: True, sampler=_Sampler(), monotonic_fn=lambda: clock[0], thread_time_fn=lambda: thread_clock[0]
        )
        self.assertTrue(collector.sample_if_due())
        clock[0], thread_clock[0] = 4.5, 0.0
        started = collector.begin_duration(DURATION_CONTROLLER_PROCESS)
        clock[0], thread_clock[0] = 5.0, 0.1
        collector.finish_duration(DURATION_CONTROLLER_PROCESS, started)
        self.assertTrue(collector.sample_if_due())
        clock[0], thread_clock[0] = 9.9, 0.1
        started = collector.begin_duration(DURATION_CONTROLLER_PROCESS)
        clock[0], thread_clock[0] = 10.0, 0.11
        collector.finish_duration(DURATION_CONTROLLER_PROCESS, started)
        self.assertTrue(collector.sample_if_due())
        samples = collector.snapshot()["samples"]
        first_window = samples[1]["stage_window"]["metrics"][DURATION_CONTROLLER_PROCESS]
        second_window = samples[2]["stage_window"]["metrics"][DURATION_CONTROLLER_PROCESS]
        self.assertEqual(1, first_window["count"])
        self.assertEqual(10.0, first_window["wall_time_percent"])
        self.assertEqual(2.0, first_window["cpu_percent_one_core"])
        self.assertEqual(0.2, first_window["calls_per_second"])
        self.assertEqual(2.0, second_window["wall_time_percent"])
        self.assertEqual(0.2, second_window["cpu_percent_one_core"])
        self.assertEqual(10.0, collector.snapshot()["stage_window_peaks"][DURATION_CONTROLLER_PROCESS]["wall_time_percent"])

    def test_throwing_clock_does_not_escape_timing_helpers(self):
        # Constructor samples its window clock, so replace the callable after
        # construction solely to prove wrapper isolation.
        collector = PerformanceDiagnosticsCollector(lambda: True)
        collector._PerformanceDiagnosticsCollector__monotonic = lambda: (_ for _ in ()).throw(RuntimeError("clock"))
        self.assertIsNone(collector.begin_duration(DURATION_CONTROLLER_PROCESS))
        collector.finish_duration(DURATION_CONTROLLER_PROCESS, (0.0, 0.0))
        self.assertFalse(collector.sample_if_due())

    def test_scanner_stage_attribution_and_active_longest_stage_are_fixed_name(self):
        clock = [10.0]
        thread_clock = [3.0]
        collector = PerformanceDiagnosticsCollector(
            lambda: True,
            monotonic_fn=lambda: clock[0],
            thread_time_fn=lambda: thread_clock[0],
        )
        started = collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        self.assertIsNotNone(started)
        clock[0] = 12.5
        thread_clock[0] = 3.25
        active = collector.snapshot()
        self.assertEqual(1, active["active_stage_counts"][DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL])
        self.assertEqual(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, active["active_stage"]["name"])
        self.assertEqual(2.5, active["active_stage"]["wall_seconds"])
        collector.finish_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, started)
        result = collector.snapshot()
        self.assertEqual(0, result["active_stage_counts"][DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL])
        self.assertEqual(1, result["durations"][DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL]["count"])
        self.assertIsNone(collector.begin_duration("/private/path"))
        collector.observe_duration("/private/path", 4.0)
        self.assertNotIn("/private/path", str(result))

    def test_model_update_substages_are_fixed_registered_metrics(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        metrics = (
            DURATION_MODEL_UPDATE_STATE_PREPARATION,
            DURATION_MODEL_UPDATE_SCAN_INTAKE,
            DURATION_MODEL_UPDATE_STATUS_INGESTION,
            DURATION_MODEL_UPDATE_BUILDER_SYNC,
            DURATION_MODEL_UPDATE_LIFECYCLE_MAINTENANCE,
            DURATION_MODEL_UPDATE_BUILD_FINALIZATION,
        )
        tokens = [collector.begin_duration(metric) for metric in metrics]
        snapshot = collector.snapshot()
        self.assertEqual(metrics, tuple(
            metric for metric in metrics if snapshot["active_stage_counts"][metric] == 1
        ))
        for metric, token in zip(metrics, tokens):
            collector.finish_duration(metric, token)

    def test_model_builder_setter_durations_are_fixed_registered_metrics(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        metrics = (
            DURATION_MODEL_BUILDER_SET_LOCAL_FILES,
            DURATION_MODEL_BUILDER_SET_REMOTE_FILES,
            DURATION_MODEL_BUILDER_SET_ACTIVE_FILES,
            DURATION_MODEL_BUILDER_SET_LFTP_STATUSES,
            DURATION_MODEL_BUILDER_SET_STOPPED_FILES,
        )
        tokens = [collector.begin_duration(metric) for metric in metrics]
        snapshot = collector.snapshot()
        self.assertEqual(metrics, tuple(
            metric for metric in metrics if snapshot["active_stage_counts"][metric] == 1
        ))
        for metric, token in zip(metrics, tokens):
            collector.finish_duration(metric, token)
        durations = collector.snapshot()["durations"]
        for metric in metrics:
            self.assertEqual(1, durations[metric]["count"])

    def test_four_concurrent_scanner_stages_cleanup_after_finish_and_exception(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        ready = threading.Event()
        release = threading.Event()
        started_tokens = []
        lock = threading.Lock()

        def worker():
            token = collector.begin_duration(DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION)
            with lock:
                started_tokens.append(token)
                if len(started_tokens) == 4:
                    ready.set()
            release.wait()
            collector.finish_duration(DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION, token)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.assertTrue(ready.wait(timeout=2.0))
        active = collector.snapshot()
        self.assertEqual(4, active["active_stage_counts"][DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION])
        release.set()
        for thread in threads:
            thread.join()
        finished = collector.snapshot()
        self.assertEqual(0, finished["active_stage_counts"][DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION])
        self.assertEqual(4, finished["durations"][DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION]["count"])

    def test_begin_reset_race_cannot_insert_a_stale_span(self):
        entered = threading.Event()
        release = threading.Event()
        block_next = [False]

        def monotonic():
            if block_next[0]:
                block_next[0] = False
                entered.set()
                release.wait(timeout=2.0)
            return 1.0

        collector = PerformanceDiagnosticsCollector(lambda: True, monotonic_fn=monotonic)
        block_next[0] = True
        token_holder = []
        thread = threading.Thread(target=lambda: token_holder.append(
            collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        ))
        thread.start()
        self.assertTrue(entered.wait(timeout=2.0))
        collector.reset()
        release.set()
        thread.join(timeout=2.0)
        self.assertEqual([None], token_holder)
        snapshot = collector.snapshot()
        self.assertEqual(2, snapshot["session"])
        self.assertEqual(0, sum(snapshot["active_stage_counts"].values()))

    def test_equal_age_active_stage_uses_stable_metric_order(self):
        collector = PerformanceDiagnosticsCollector(lambda: True, monotonic_fn=lambda: 1.0)
        first = collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        second = collector.begin_duration(DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL,
            collector.snapshot()["active_stage"]["name"],
        )
        collector.finish_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, first)
        collector.finish_duration(DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION, second)

    def test_finish_metric_mismatch_cleans_token_without_wrong_bucket(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        token = collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        collector.finish_duration(DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION, token)
        snapshot = collector.snapshot()
        self.assertEqual(0, sum(snapshot["active_stage_counts"].values()))
        self.assertEqual({}, snapshot["durations"])

    def test_invalid_metric_token_finish_cleans_owned_span_without_recording(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        token = collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        collector.finish_duration("/private/path", token)
        snapshot = collector.snapshot()
        self.assertEqual(0, sum(snapshot["active_stage_counts"].values()))
        self.assertEqual({}, snapshot["durations"])

    def test_legacy_finish_rejects_private_metric_without_exporting_it(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        collector.finish_duration("/private/path", (0.0, 0.0))
        self.assertNotIn("/private/path", str(collector.snapshot()))

    def test_legacy_finish_reset_race_cannot_append_after_reset(self):
        entered = threading.Event()
        release = threading.Event()
        block_next = [False]

        def monotonic():
            if block_next[0]:
                block_next[0] = False
                entered.set()
                release.wait(timeout=2.0)
            return 1.0

        collector = PerformanceDiagnosticsCollector(lambda: True, monotonic_fn=monotonic)
        block_next[0] = True
        thread = threading.Thread(target=lambda: collector.finish_duration(
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, (0.0, 0.0)
        ))
        thread.start()
        self.assertTrue(entered.wait(timeout=2.0))
        collector.reset()
        release.set()
        thread.join(timeout=2.0)
        self.assertEqual({}, collector.snapshot()["durations"])

    def test_legacy_finish_disable_interleaving_does_not_record(self):
        enabled = [True]
        entered = threading.Event()
        release = threading.Event()
        block_next = [False]

        def monotonic():
            if block_next[0]:
                block_next[0] = False
                entered.set()
                release.wait(timeout=2.0)
            return 1.0

        collector = PerformanceDiagnosticsCollector(lambda: enabled[0], monotonic_fn=monotonic)
        block_next[0] = True
        thread = threading.Thread(target=lambda: collector.finish_duration(
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, (0.0, 0.0)
        ))
        thread.start()
        self.assertTrue(entered.wait(timeout=2.0))
        enabled[0] = False
        release.set()
        thread.join(timeout=2.0)
        self.assertEqual({}, collector.snapshot()["durations"])

    def test_finish_reset_race_cannot_resurrect_a_pre_reset_duration(self):
        entered = threading.Event()
        release = threading.Event()
        block_next = [False]

        def monotonic():
            if block_next[0]:
                block_next[0] = False
                entered.set()
                release.wait(timeout=2.0)
            return 2.0

        collector = PerformanceDiagnosticsCollector(lambda: True, monotonic_fn=monotonic)
        token = collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        block_next[0] = True
        thread = threading.Thread(target=lambda: collector.finish_duration(
            DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, token
        ))
        thread.start()
        self.assertTrue(entered.wait(timeout=2.0))
        collector.reset()
        release.set()
        thread.join(timeout=2.0)
        snapshot = collector.snapshot()
        self.assertEqual(2, snapshot["session"])
        self.assertEqual({}, snapshot["durations"])
        self.assertEqual(0, sum(snapshot["active_stage_counts"].values()))

    def test_observe_reset_race_cannot_append_to_the_new_session(self):
        entered = threading.Event()
        release = threading.Event()
        block_next = [True]
        original_numeric = performance_diagnostics._numeric

        def numeric(value):
            if block_next[0] and value == 3.0:
                block_next[0] = False
                entered.set()
                release.wait(timeout=2.0)
            return original_numeric(value)

        collector = PerformanceDiagnosticsCollector(lambda: True)
        with patch.object(performance_diagnostics, "_numeric", side_effect=numeric):
            thread = threading.Thread(target=lambda: collector.observe_duration(
                DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, 3.0, 1.0
            ))
            thread.start()
            self.assertTrue(entered.wait(timeout=2.0))
            collector.reset()
            release.set()
            thread.join(timeout=2.0)
        snapshot = collector.snapshot()
        self.assertEqual(2, snapshot["session"])
        self.assertEqual({}, snapshot["durations"])

    def test_active_span_cap_reports_mixed_metric_drops_without_stuck_counts(self):
        release = threading.Event()
        ready = threading.Event()
        tokens = []
        tokens_lock = threading.Lock()

        def worker(worker_index):
            local_tokens = []
            for index in range(10):
                metric = (
                    DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL
                    if (worker_index + index) % 2 == 0
                    else DURATION_LOCAL_SCAN_PROGRESS_PUBLICATION
                )
                local_tokens.append((metric, collector.begin_duration(metric)))
            with tokens_lock:
                tokens.extend(local_tokens)
                if len(tokens) >= 40:
                    ready.set()
            release.wait(timeout=2.0)

        collector = PerformanceDiagnosticsCollector(lambda: True)
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        self.assertTrue(ready.wait(timeout=2.0))
        active = collector.snapshot()
        self.assertEqual(32, sum(active["active_stage_counts"].values()))
        self.assertEqual(8, active["counters"]["duration_spans_dropped"])
        release.set()
        for thread in threads:
            thread.join(timeout=2.0)
        for metric, token in tokens:
            collector.finish_duration(metric, token)
        self.assertEqual(0, sum(collector.snapshot()["active_stage_counts"].values()))

    def test_hot_disable_clears_active_stage_counts_and_disabled_begin_is_fast(self):
        enabled = [True]
        thread_calls = [0]

        def thread_time():
            thread_calls[0] += 1
            return 1.0

        collector = PerformanceDiagnosticsCollector(lambda: enabled[0], thread_time_fn=thread_time)
        started = collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL)
        self.assertIsNotNone(started)
        enabled[0] = False
        self.assertEqual(0, collector.snapshot()["active_stage_counts"][DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL])
        before = thread_calls[0]
        self.assertIsNone(collector.begin_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL))
        self.assertEqual(before, thread_calls[0])
        collector.finish_duration(DURATION_LOCAL_SCAN_FILESYSTEM_TRAVERSAL, started)

    def test_malformed_sampler_result_is_failure_isolated(self):
        class MalformedSampler:
            def sample(self):
                return object()

        collector = PerformanceDiagnosticsCollector(lambda: True, sampler=MalformedSampler())
        self.assertFalse(collector.sample_if_due())
        self.assertEqual(1, collector.snapshot()["counters"]["sampler_failures"])

    def test_rejects_non_finite_and_oversized_values_from_export(self):
        collector = PerformanceDiagnosticsCollector(lambda: True)
        collector.record_sample({"process_cpu_percent_one_core": math.inf, "process_rss_bytes": 10 ** 100000})
        snapshot = collector.snapshot()
        self.assertIsNone(snapshot["samples"][0]["process_cpu_percent_one_core"])
        self.assertIsNone(snapshot["samples"][0]["process_rss_bytes"])
        exported = collector.export_snapshot()
        self.assertLessEqual(len(__import__("json").dumps(exported, allow_nan=False).encode("utf-8")), 48 * 1024)

    def test_extreme_timing_ratio_stays_finite_and_json_safe(self):
        clock = [0.0]
        collector = PerformanceDiagnosticsCollector(lambda: True, monotonic_fn=lambda: clock[0])
        collector.observe_duration(DURATION_CONTROLLER_PROCESS, (1 << 63) - 1, (1 << 63) - 1)
        clock[0] = 1e-300
        collector.record_sample({}, close_window_at=clock[0])
        window = collector.snapshot()["samples"][0]["stage_window"]["metrics"][DURATION_CONTROLLER_PROCESS]
        self.assertIsNone(window["wall_time_percent"])
        self.assertIsNone(window["cpu_percent_one_core"])
        __import__("json").dumps(collector.export_snapshot(), allow_nan=False)


class TestProcessContainerSampler(unittest.TestCase):
    def test_linux_proc_and_cgroup_parsers_and_cpu_delta(self):
        texts = {
            "/proc/self/stat": "1 (python worker) S " + "0 " * 20 + "10 ",
            "/proc/self/status": "RssAnon:\t12 kB\nThreads:\t7\n",
            "/proc/self/cgroup": "0::/seed\n",
            "/sys/fs/cgroup/seed/cpu.stat": "usage_usec 99\n",
            "/sys/fs/cgroup/seed/memory.current": "123\n",
            "/sys/fs/cgroup/seed/memory.stat": "anon 11\nfile 12\nkernel 13\n",
        }
        tick = [0.0]
        cpu = [1.0]
        sampler = ProcessContainerSampler(
            lambda: tick[0], lambda: cpu[0], lambda: "Linux",
            lambda path: texts[path.replace("\\", "/")], lambda _: ["1"]
        )
        first = sampler.sample()
        tick[0], cpu[0] = 5.0, 1.5
        second = sampler.sample()
        self.assertIsNone(first["process_cpu_percent_one_core"])
        self.assertEqual(10.0, second["process_cpu_percent_one_core"])
        self.assertIsNone(first["cgroup_cpu_percent_one_core"])
        self.assertEqual(0.0, second["cgroup_cpu_percent_one_core"])
        self.assertEqual(10 * 4096, second["process_rss_bytes"])
        self.assertEqual(12 * 1024, second["process_anon_bytes"])
        self.assertEqual(7, second["process_threads"])
        self.assertEqual(123, second["cgroup_memory_current_bytes"])

    def test_windows_degrades_without_procfs(self):
        sampler = ProcessContainerSampler(platform_system=lambda: "Windows")
        sample = sampler.sample()
        self.assertIsNone(sample["process_rss_bytes"])
        self.assertIn("process_cpu_percent_one_core", sample)
