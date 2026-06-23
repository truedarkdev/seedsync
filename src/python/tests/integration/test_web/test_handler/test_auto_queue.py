# Copyright 2017, Inderpreet Singh, All rights reserved.

import json
import threading
from urllib.parse import quote

from controller import AutoQueuePattern
from controller.auto_queue import AutoQueuePersistListener
from tests.integration.test_web.test_web_app import BaseTestWebApp


class ThrowingAutoQueuePersistListener(AutoQueuePersistListener):
    def __init__(self, add_error=None, remove_error=None):
        super().__init__()
        self._add_error = add_error
        self._remove_error = remove_error

    def pattern_added(self, pattern):
        super().pattern_added(pattern)
        if self._add_error is not None:
            raise self._add_error

    def pattern_removed(self, pattern):
        super().pattern_removed(pattern)
        if self._remove_error is not None:
            raise self._remove_error


class TestAutoQueueHandler(BaseTestWebApp):
    def __assert_json_response(self, response):
        self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])

    def __assert_plain_text_response(self, response):
        self.assertTrue(response.headers["Content-Type"].startswith("text/plain"))
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])

    def test_get(self):
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="one"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="t wo"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="thr'ee"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="fo\"ur"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="fi%ve"))
        resp = self.test_app.get("/server/autoqueue/get")
        self.assertEqual(200, resp.status_int)
        self.__assert_json_response(resp)
        json_list = json.loads(resp.text)
        self.assertEqual(5, len(json_list))
        self.assertIn({"pattern": "one"}, json_list)
        self.assertIn({"pattern": "t wo"}, json_list)
        self.assertIn({"pattern": "thr'ee"}, json_list)
        self.assertIn({"pattern": "fo\"ur"}, json_list)
        self.assertIn({"pattern": "fi%ve"}, json_list)

    def test_get_is_ordered(self):
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="a"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="b"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="c"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="d"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(pattern="e"))
        resp = self.test_app.get("/server/autoqueue/get")
        self.assertEqual(200, resp.status_int)
        self.__assert_json_response(resp)
        json_list = json.loads(resp.text)
        self.assertEqual(5, len(json_list))
        self.assertEqual([
            {"pattern": "a"},
            {"pattern": "b"},
            {"pattern": "c"},
            {"pattern": "d"},
            {"pattern": "e"}
        ], json_list)

    def test_add_good(self):
        resp = self.test_app.post("/server/autoqueue/add/one")
        self.assertEqual(200, resp.status_int)
        self.assertEqual(1, len(self.auto_queue_persist.patterns))
        self.assertIn(AutoQueuePattern("one"), self.auto_queue_persist.patterns)

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/add/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(2, len(self.auto_queue_persist.patterns))
        self.assertIn(AutoQueuePattern("/value/with/slashes"), self.auto_queue_persist.patterns)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/add/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(3, len(self.auto_queue_persist.patterns))
        self.assertIn(AutoQueuePattern(" value with spaces"), self.auto_queue_persist.patterns)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/add/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(4, len(self.auto_queue_persist.patterns))
        self.assertIn(AutoQueuePattern("value'with'singlequote"), self.auto_queue_persist.patterns)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/add/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(5, len(self.auto_queue_persist.patterns))
        self.assertIn(AutoQueuePattern("value\"with\"doublequote"), self.auto_queue_persist.patterns)

    def test_add_get_requests_no_longer_mutate(self):
        resp = self.test_app.get("/server/autoqueue/add/one", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual(0, len(self.auto_queue_persist.patterns))

    def test_add_double(self):
        resp = self.test_app.post("/server/autoqueue/add/one")
        self.assertEqual(200, resp.status_int)
        resp = self.test_app.post("/server/autoqueue/add/one", expect_errors=True)
        self.assertEqual(409, resp.status_int)
        self.assertEqual("Auto-queue pattern 'one' already exists.", resp.text)
        self.__assert_plain_text_response(resp)

    def test_add_serializes_concurrent_requests_under_write_lock(self):
        handler = self.web_app_builder.auto_queue_handler
        enter_to_add = threading.Event()
        release_to_add = threading.Event()
        results = {}

        class ProbingLock:
            def __init__(self):
                self._lock = threading.Lock()
                self.second_acquire_attempted = threading.Event()

            def __enter__(self):
                if self._lock.locked():
                    self.second_acquire_attempted.set()
                self._lock.acquire()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                self._lock.release()
                return False

        class BlockingListener(AutoQueuePersistListener):
            def pattern_added(self, pattern):
                super().pattern_added(pattern)
                enter_to_add.set()
                if not release_to_add.wait(timeout=5):
                    results["release_timeout"] = True

        handler._AutoQueueHandler__write_lock = ProbingLock()
        self.auto_queue_persist.add_listener(BlockingListener())

        def writer_a():
            response = handler._AutoQueueHandler__handle_add_autoqueue("onepattern")
            results["a"] = response.status_code

        def writer_b():
            response = handler._AutoQueueHandler__handle_add_autoqueue("onepattern")
            results["b"] = response.status_code

        t_a = threading.Thread(target=writer_a)
        t_a.start()
        self.assertTrue(enter_to_add.wait(timeout=5))

        t_b = threading.Thread(target=writer_b)
        t_b.start()
        self.assertTrue(handler._AutoQueueHandler__write_lock.second_acquire_attempted.wait(timeout=5))
        self.assertNotIn("b", results)

        release_to_add.set()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        self.assertFalse(t_a.is_alive())
        self.assertFalse(t_b.is_alive())
        self.assertFalse(results.get("release_timeout", False))
        self.assertEqual(200, results["a"])
        self.assertEqual(409, results["b"])
        self.assertIn(AutoQueuePattern("onepattern"), self.auto_queue_persist.patterns)

    def test_add_empty_value(self):
        uri = quote(quote("  ", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/add/" + uri, expect_errors=True)
        self.assertEqual(400, resp.status_int)
        self.__assert_plain_text_response(resp)
        self.assertEqual(0, len(self.auto_queue_persist.patterns))

        resp = self.test_app.post("/server/autoqueue/add/", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual(0, len(self.auto_queue_persist.patterns))

    def test_remove_good(self):
        self.auto_queue_persist.add_pattern(AutoQueuePattern("one"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern("/value/with/slashes"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern(" value with spaces"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern("value'with'singlequote"))
        self.auto_queue_persist.add_pattern(AutoQueuePattern("value\"with\"doublequote"))

        resp = self.test_app.post("/server/autoqueue/remove/one")
        self.assertEqual(200, resp.status_int)
        self.assertEqual(4, len(self.auto_queue_persist.patterns))
        self.assertNotIn(AutoQueuePattern("one"), self.auto_queue_persist.patterns)

        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/remove/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(3, len(self.auto_queue_persist.patterns))
        self.assertNotIn(AutoQueuePattern("/value/with/slashes"), self.auto_queue_persist.patterns)

        uri = quote(quote(" value with spaces", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/remove/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(2, len(self.auto_queue_persist.patterns))
        self.assertNotIn(AutoQueuePattern(" value with spaces"), self.auto_queue_persist.patterns)

        uri = quote(quote("value'with'singlequote", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/remove/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(1, len(self.auto_queue_persist.patterns))
        self.assertNotIn(AutoQueuePattern("value'with'singlequote"), self.auto_queue_persist.patterns)

        uri = quote(quote("value\"with\"doublequote", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/remove/" + uri)
        self.assertEqual(200, resp.status_int)
        self.assertEqual(0, len(self.auto_queue_persist.patterns))
        self.assertNotIn(AutoQueuePattern("value\"with\"doublequote"), self.auto_queue_persist.patterns)

    def test_remove_get_requests_no_longer_mutate(self):
        self.auto_queue_persist.add_pattern(AutoQueuePattern("one"))
        resp = self.test_app.get("/server/autoqueue/remove/one", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertIn(AutoQueuePattern("one"), self.auto_queue_persist.patterns)

    def test_remove_non_existing(self):
        resp = self.test_app.post("/server/autoqueue/remove/one", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual("Auto-queue pattern 'one' doesn't exist.", resp.text)
        self.__assert_plain_text_response(resp)

    def test_add_failure_rolls_back_listener_state_on_non_oserror(self):
        listener = ThrowingAutoQueuePersistListener(add_error=RuntimeError("serialize boom"))
        self.auto_queue_persist.add_listener(listener)

        resp = self.test_app.post("/server/autoqueue/add/onepattern", expect_errors=True)

        self.assertEqual(500, resp.status_int)
        self.assertEqual("Failed to persist auto-queue", resp.text)
        self.__assert_plain_text_response(resp)
        self.assertNotIn(AutoQueuePattern("onepattern"), self.auto_queue_persist.patterns)
        self.assertNotIn(AutoQueuePattern("onepattern"), listener.new_patterns)

    def test_remove_failure_rolls_back_listener_state_on_non_oserror(self):
        listener = ThrowingAutoQueuePersistListener(remove_error=RuntimeError("serialize boom"))
        self.auto_queue_persist.add_listener(listener)
        add_resp = self.test_app.post("/server/autoqueue/add/onepattern")
        self.assertEqual(200, add_resp.status_int)
        self.assertIn(AutoQueuePattern("onepattern"), self.auto_queue_persist.patterns)
        self.assertIn(AutoQueuePattern("onepattern"), listener.new_patterns)

        resp = self.test_app.post("/server/autoqueue/remove/onepattern", expect_errors=True)

        self.assertEqual(500, resp.status_int)
        self.assertEqual("Failed to persist auto-queue", resp.text)
        self.__assert_plain_text_response(resp)
        self.assertIn(AutoQueuePattern("onepattern"), self.auto_queue_persist.patterns)
        self.assertIn(AutoQueuePattern("onepattern"), listener.new_patterns)

    def test_remove_empty_value(self):
        uri = quote(quote("  ", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/remove/" + uri, expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual("Auto-queue pattern '  ' doesn't exist.", resp.text)
        self.assertEqual(0, len(self.auto_queue_persist.patterns))

        resp = self.test_app.post("/server/autoqueue/remove/", expect_errors=True)
        self.assertEqual(404, resp.status_int)
        self.assertEqual(0, len(self.auto_queue_persist.patterns))

    def test_add_response_is_plain_text_and_nosniff(self):
        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/add/" + uri)

        self.assertEqual(200, resp.status_int)
        self.assertEqual("Added auto-queue pattern '/value/with/slashes'.", resp.text)
        self.__assert_plain_text_response(resp)

    def test_remove_response_is_plain_text_and_nosniff(self):
        self.auto_queue_persist.add_pattern(AutoQueuePattern("/value/with/slashes"))
        uri = quote(quote("/value/with/slashes", safe=""), safe="")
        resp = self.test_app.post("/server/autoqueue/remove/" + uri)

        self.assertEqual(200, resp.status_int)
        self.assertEqual("Removed auto-queue pattern '/value/with/slashes'.", resp.text)
        self.__assert_plain_text_response(resp)
