# Copyright 2026, SeedSync Contributors, All rights reserved.

import threading
import unittest
import math

from common.performance_diagnostics import (
    DURATION_CONTROLLER_PROCESS,
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
