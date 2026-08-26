# Copyright 2017, Inderpreet Singh, All rights reserved.

import logging
import sys
import unittest
from unittest.mock import MagicMock, call

from common import BreadcrumbTraceCollector, overrides
from model import ActiveProgressOverlay, Model, ModelFile, IModelListener, ModelError


class DummyModelListener(IModelListener):
    @overrides(IModelListener)
    def file_added(self, file: ModelFile):
        pass

    @overrides(IModelListener)
    def file_removed(self, file: ModelFile):
        pass

    @overrides(IModelListener)
    def file_updated(self, old_file: ModelFile, new_file: ModelFile):
        pass


class TestLftpModel(unittest.TestCase):
    def test_published_root_snapshot_keeps_effective_overlay_across_replacement(self):
        file = ModelFile("active", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.model.add_file(file)
        self.assertEqual(
            ({file.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                {file.file_id: ActiveProgressOverlay(53, 530, 12, 3)},
                {file.file_id: (1, "get")}, lambda _: True,
            ),
        )
        replacement = ModelFile("active", False)
        replacement.state = ModelFile.State.DOWNLOADING
        replacement.is_stoppable = True
        replacement.download_progress = 1
        replacement.transferred_size = 10
        self.model.apply_with_active_progress_retained(
            lambda: self.model.update_file(replacement)
        )

        published = self.model.published_file(file.file_id)

        self.assertIsNotNone(published)
        assert published is not None
        self.assertEqual(53, published.download_progress)
        self.assertEqual(530, published.transferred_size)
        self.assertEqual(12, published.downloading_speed)
        self.assertEqual(3, published.eta)
        self.assertIsNot(published, replacement)

    def test_retired_equal_root_refreshes_base_without_unrelated_notification(self):
        active = ModelFile("active", False)
        active.state = ModelFile.State.DOWNLOADING
        active.is_stoppable = True
        active.download_progress = 10
        active.transferred_size = 10
        other = ModelFile("other", False)
        other.state = ModelFile.State.DOWNLOADING
        other.is_stoppable = True
        other.download_progress = 25
        other.transferred_size = 25
        self.model.add_file(active)
        self.model.add_file(other)
        overlays = {
            active.file_id: ActiveProgressOverlay(60, 60, 12, 6),
            other.file_id: ActiveProgressOverlay(25, 25, 7, 9),
        }
        identities = {
            active.file_id: (7, "pget"),
            other.file_id: (8, "pget"),
        }
        self.assertEqual(
            ({active.file_id, other.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                overlays, identities, lambda _: True,
            ),
        )
        listener = MagicMock()
        self.model.add_listener(listener)

        # The equal root models the active-delta replacement that carries a
        # changed job identity or explicit zero but no topology difference.
        self.model.apply_with_active_progress_retained(
            lambda: None, retire_file_ids={active.file_id},
        )

        published = self.model.published_file(active.file_id)
        self.assertIsNotNone(published)
        assert published is not None
        self.assertEqual((10, 10), (published.transferred_size, published.download_progress))
        self.assertIsNone(self.model.active_progress_overlay(active.file_id))
        self.assertEqual(overlays[other.file_id], self.model.active_progress_overlay(other.file_id))
        self.assertEqual(
            (25, 25),
            (
                self.model.published_file(other.file_id).transferred_size,
                self.model.published_file(other.file_id).download_progress,
            ),
        )
        self.assertEqual(1, listener.model_version_changed.call_count)
        self.assertEqual(active.file_id, listener.model_version_changed.call_args.args[2])
        self.assertEqual(1, listener.model_version_published.call_count)
        self.assertEqual(active.file_id, listener.model_version_published.call_args.args[3])
        listener.file_updated.assert_not_called()

    def test_lftp_root_counter_publish_rejects_stale_lifecycle_and_clears_projection(self):
        file = ModelFile("active", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.model.add_file(file)
        overlay = ActiveProgressOverlay(25, 25, 10, 8)
        changed, outcome = self.model.publish_active_lftp_root_counters(
            {file.file_id: overlay}, {file.file_id: (1, "get")}, lambda _: True,
        )
        self.assertEqual(({file.file_id}, "accepted"), (changed, outcome))
        changed, outcome = self.model.publish_active_lftp_root_counters(
            {file.file_id: overlay}, {file.file_id: (1, "get")}, lambda _: False,
        )
        self.assertEqual((set(), "lifecycle_epoch"), (changed, outcome))
        self.assertIsNone(self.model.active_progress_overlay(file.file_id))

    def test_lftp_root_counter_publish_floors_stale_positive_same_job_counters(self):
        file = ModelFile("active", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.model.add_file(file)
        identity = {file.file_id: (7, "pget")}

        first = ActiveProgressOverlay(51, 17153537, 120, 6)
        stale = ActiveProgressOverlay(49, 16467263, 80, 9)
        latest = ActiveProgressOverlay(54, 18127086, 140, 3)
        self.assertEqual(
            ({file.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                {file.file_id: first}, identity, lambda _: True,
            ),
        )
        self.assertEqual(first, self.model.active_progress_overlay(file.file_id))

        self.assertEqual(
            ({file.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                {file.file_id: stale}, identity, lambda _: True,
            ),
        )
        self.assertEqual(
            ActiveProgressOverlay(51, 17153537, 80, 9),
            self.model.active_progress_overlay(file.file_id),
        )

        bytes_advance = ActiveProgressOverlay(49, 17500000, 95, 7)
        self.assertEqual(
            ({file.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                {file.file_id: bytes_advance}, identity, lambda _: True,
            ),
        )
        self.assertEqual(
            ActiveProgressOverlay(51, 17500000, 95, 7),
            self.model.active_progress_overlay(file.file_id),
        )

        self.assertEqual(
            ({file.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                {file.file_id: latest}, identity, lambda _: True,
            ),
        )
        self.assertEqual(latest, self.model.active_progress_overlay(file.file_id))

        reset = ActiveProgressOverlay(0, 0, 0, 0)
        self.assertEqual(
            ({file.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                {file.file_id: reset}, identity, lambda _: True,
            ),
        )
        self.assertEqual(reset, self.model.active_progress_overlay(file.file_id))

    def test_lftp_root_counter_publish_preserves_lifecycle_state_and_rejects_display_union(self):
        file = ModelFile("active", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.model.add_file(file)
        changed, outcome = self.model.publish_active_lftp_root_counters(
            {file.file_id: ActiveProgressOverlay(25, 25, 10, 8)},
            {file.file_id: (1, "get")}, lambda _: True,
        )
        self.assertEqual(({file.file_id}, "accepted"), (changed, outcome))
        self.assertEqual(ModelFile.State.DOWNLOADING, self.model.get_file(file.file_id).state)
        file.display_size_total = 100
        changed, outcome = self.model.publish_active_lftp_root_counters(
            {file.file_id: ActiveProgressOverlay(26, 26, 10, 8)},
            {file.file_id: (1, "get")}, lambda _: True,
        )
        self.assertEqual((set(), "root_authority"), (changed, outcome))
        self.assertIsNone(self.model.active_progress_overlay(file.file_id))

    def test_lftp_root_counter_publish_rejects_late_old_job_after_new_job(self):
        file = ModelFile("active", False)
        file.state = ModelFile.State.DOWNLOADING
        file.is_stoppable = True
        self.model.add_file(file)
        first = ActiveProgressOverlay(25, 25, 10, 8)
        second = ActiveProgressOverlay(50, 50, 11, 7)
        self.assertEqual(( {file.file_id}, "accepted"), self.model.publish_active_lftp_root_counters(
            {file.file_id: first}, {file.file_id: (1, "get")}, lambda _: True,
        ))
        self.model.clear_active_progress_overlays()
        self.assertEqual(({file.file_id}, "accepted"), self.model.publish_active_lftp_root_counters(
            {file.file_id: second}, {file.file_id: (2, "get")}, lambda _: True,
        ))
        self.assertEqual((set(), "job_identity"), self.model.publish_active_lftp_root_counters(
            {file.file_id: first}, {file.file_id: (1, "get")}, lambda _: True,
        ))
        self.assertEqual(second, self.model.active_progress_overlay(file.file_id))

    def test_selected_overlay_clear_and_restore_preserve_only_protected_root(self):
        first = ModelFile("first", False)
        second = ModelFile("second", False)
        for file in (first, second):
            file.state = ModelFile.State.DOWNLOADING
            file.is_stoppable = True
            self.model.add_file(file)
        overlays = {
            first.file_id: ActiveProgressOverlay(50, 50, 10, 8),
            second.file_id: ActiveProgressOverlay(60, 60, 11, 7),
        }
        identities = {
            first.file_id: (2, "get"),
            second.file_id: (2, "get"),
        }
        self.assertEqual(
            ({first.file_id, second.file_id}, "accepted"),
            self.model.publish_active_lftp_root_counters(
                overlays, identities, lambda _: True,
            ),
        )
        self.assertEqual(
            {second.file_id},
            self.model.clear_active_progress_overlays_except({first.file_id}),
        )
        self.assertEqual(overlays[first.file_id], self.model.active_progress_overlay(first.file_id))
        self.assertIsNone(self.model.active_progress_overlay(second.file_id))

        self.model.update_file(self.model.get_file(second.file_id))
        self.assertIsNone(self.model.active_progress_overlay(first.file_id))
        self.assertEqual(
            {first.file_id},
            self.model.restore_active_progress_overlays(
                {first.file_id: overlays[first.file_id]},
                {first.file_id: identities[first.file_id]},
            ),
        )
        self.assertEqual(overlays[first.file_id], self.model.active_progress_overlay(first.file_id))

    def test_version_callbacks_keep_captured_versions_across_reentrant_listener_mutation(self):
        first = ModelFile("first", False)
        second = ModelFile("second", False)
        observed = []

        class ReentrantListener(DummyModelListener):
            def model_version_changed(self, version, path_pair_id, file_id):
                if file_id == first.file_id:
                    self_model.add_file(second)

            def model_version_published(self, scope_version, global_version, path_pair_id, file_id):
                observed.append((file_id, global_version))

        self_model = self.model
        self.model.add_listener(ReentrantListener())
        self.model.add_file(first)

        self.assertIn((first.file_id, 1), observed)
        self.assertIn((second.file_id, 2), observed)

    def test_publication_mapping_survives_later_caller_exception(self):
        trace = BreadcrumbTraceCollector(
            lambda: True, policy={"default": "off", "rules": {"model.progress": "debug"}},
        )
        self.model.set_version_publication_callback(
            lambda global_version, scope_version: trace.record_progress_lineage(
                "lftp-poll:0123456789abcdef", "model_mutation",
                {"outcome": "mutated", "model_version": global_version,
                 "scope_version": scope_version},
            )
        )
        try:
            self.model.add_file(ModelFile("published", False))
            self.model.add_file(ModelFile("published-second", False))
            raise RuntimeError("later updater work failed")
        except RuntimeError:
            pass
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            1, "scoped_stream_emit", {"scope_version": 1},
        ))
        self.assertTrue(trace.record_progress_lineage_for_model_version(
            2, "scoped_stream_emit", {"scope_version": 2},
        ))

    def setUp(self):
        logger = logging.getLogger(TestLftpModel.__name__)
        handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        handler.setFormatter(formatter)
        self.model = Model()
        self.model.set_base_logger(logger)

    def test_add_file(self):
        file = ModelFile("test", False)
        self.model.add_file(file)
        recv_file = self.model.get_file("test")
        self.assertEqual("test", recv_file.name)

    def test_get_unknown_file(self):
        with self.assertRaises(ModelError):
            self.model.get_file("test")

    def test_remove_file(self):
        file = ModelFile("test", False)
        self.model.add_file(file)
        self.model.remove_file("test")
        with self.assertRaises(ModelError):
            self.model.get_file("test")

    def test_remove_unknown_file(self):
        with self.assertRaises(ModelError):
            self.model.remove_file("test")

    def test_update_file(self):
        file = ModelFile("test", False)
        file.local_size = 100
        self.model.add_file(file)
        recv_file = self.model.get_file("test")
        self.assertEqual(100, recv_file.local_size)
        recv_file.local_size = 200
        self.model.update_file(recv_file)
        recv_file = self.model.get_file("test")
        self.assertEqual(200, recv_file.local_size)

    def test_update_unknown_file(self):
        file = ModelFile("test", False)
        with self.assertRaises(ModelError):
            self.model.update_file(file)

    def test_get_file_names(self):
        self.assertEqual(set(), self.model.get_file_names())
        self.model.add_file(ModelFile("a", False))
        self.assertEqual({"a"}, self.model.get_file_names())
        self.model.add_file(ModelFile("b", False))
        self.assertEqual({"a", "b"}, self.model.get_file_names())
        self.model.add_file(ModelFile("c", False))
        self.assertEqual({"a", "b", "c"}, self.model.get_file_names())
        self.model.remove_file("b")
        self.assertEqual({"a", "c"}, self.model.get_file_names())
        self.model.add_file(ModelFile("d", False))
        self.assertEqual({"a", "c", "d"}, self.model.get_file_names())

    def test_get_file_ids(self):
        self.assertEqual(set(), self.model.get_file_ids())
        file_a = ModelFile("a", False)
        self.model.add_file(file_a)
        self.assertEqual({file_a.file_id}, self.model.get_file_ids())

    def test_compose_candidate_reuses_roots_without_notifying_live_listeners(self):
        retained = ModelFile("retained", False)
        retained.path_pair_id = "pair-b"
        replaced = ModelFile("replaced", False)
        replaced.path_pair_id = "pair-a"
        self.model.add_file(retained)
        self.model.add_file(replaced)
        listener = MagicMock(spec=DummyModelListener)
        self.model.add_listener(listener)
        replacement = ModelFile("replacement", False)
        replacement.path_pair_id = "pair-a"

        candidate = Model.compose_candidate(
            self.model, {replaced.file_id}, (replacement,), 2,
        )

        self.assertEqual({retained.file_id, replacement.file_id}, candidate.get_file_ids())
        self.assertIs(retained, candidate.get_file(retained.file_id))
        self.assertEqual(2, candidate.tree_file_count)
        self.assertEqual(0, candidate.listener_count)
        listener.file_added.assert_not_called()
        listener.file_removed.assert_not_called()
        listener.file_updated.assert_not_called()

    def test_duplicate_names_can_coexist_by_file_id(self):
        file_a_movies = ModelFile("a", False)
        file_a_movies.path_pair_id = "movies"
        file_a_tv = ModelFile("a", False)
        file_a_tv.path_pair_id = "tv"
        self.model.add_file(file_a_movies)
        self.model.add_file(file_a_tv)

        self.assertEqual({"a"}, self.model.get_file_names())
        self.assertEqual(
            {file_a_movies.file_id, file_a_tv.file_id},
            self.model.get_file_ids()
        )
        self.assertEqual(file_a_movies, self.model.get_file(file_a_movies.file_id))
        self.assertEqual(file_a_tv, self.model.get_file(file_a_tv.file_id))
        with self.assertRaises(ModelError):
            self.model.get_file("a")

    def test_debug_logs_use_short_path_pair_identity(self):
        self.model.logger = MagicMock()

        file = ModelFile("test", False)
        file.path_pair_id = "ab12cd34ef56"

        self.model.add_file(file)
        self.model.update_file(file)
        self.model.remove_file(file.file_id)

        self.assertEqual(
            [
                call("LftpModel: Adding file 'test [ab12cd34]'"),
                call("LftpModel: Updating file 'test [ab12cd34]'"),
                call("LftpModel: Removing file 'test [ab12cd34]'"),
            ],
            self.model.logger.debug.call_args_list
        )

    def test_add_listener(self):
        listener = DummyModelListener()
        self.model.add_listener(listener)

    def test_remove_listener(self):
        listener = DummyModelListener()
        listener.file_added = MagicMock()

        self.model.add_listener(listener)
        file = ModelFile("test", False)
        self.model.add_file(file)
        listener.file_added.assert_called_once_with(file)

        self.model.remove_listener(listener)
        self.model.add_file(ModelFile("test2", False))
        listener.file_added.assert_called_once_with(file)

    def test_listener_file_added(self):
        listener = DummyModelListener()
        self.model.add_listener(listener)

        listener.file_added = MagicMock()

        file = ModelFile("test", False)
        self.model.add_file(file)
        # noinspection PyUnresolvedReferences
        listener.file_added.assert_called_once_with(file)

    def test_listener_file_removed(self):
        listener = DummyModelListener()
        self.model.add_listener(listener)

        listener.file_removed = MagicMock()

        file = ModelFile("test", False)
        self.model.add_file(file)
        self.model.remove_file("test")
        # noinspection PyUnresolvedReferences
        listener.file_removed.assert_called_once_with(file)

    def test_listener_file_updated(self):
        listener = DummyModelListener()
        self.model.add_listener(listener)

        listener.file_updated = MagicMock()

        old_file = ModelFile("test", False)
        old_file.local_size = 100
        self.model.add_file(old_file)
        new_file = ModelFile("test", False)
        new_file.local_size = 200
        self.model.update_file(new_file)
        # noinspection PyUnresolvedReferences
        listener.file_updated.assert_called_once_with(old_file, new_file)
