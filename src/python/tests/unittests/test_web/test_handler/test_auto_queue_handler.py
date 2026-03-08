import json
import unittest
from unittest.mock import MagicMock
from urllib.parse import quote

from controller import AutoQueuePattern, AutoQueuePersist
from web.handler.auto_queue import AutoQueueHandler


class TestAutoQueueHandlerGet(unittest.TestCase):
    def setUp(self):
        self.persist = MagicMock(spec=AutoQueuePersist)
        self.handler = AutoQueueHandler(self.persist)

    def test_get_returns_sorted_patterns(self):
        self.persist.patterns = {AutoQueuePattern("beta"), AutoQueuePattern("alpha")}

        response = self.handler._AutoQueueHandler__handle_get_autoqueue()

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [{"pattern": "alpha"}, {"pattern": "beta"}],
            json.loads(response.body),
        )


class TestAutoQueueHandlerAdd(unittest.TestCase):
    def setUp(self):
        self.persist = MagicMock(spec=AutoQueuePersist)
        self.handler = AutoQueueHandler(self.persist)

    def test_add_calls_persist_with_decoded_pattern(self):
        self.persist.patterns = set()

        response = self.handler._AutoQueueHandler__handle_add_autoqueue(
            quote("my pattern/test")
        )

        self.assertEqual(200, response.status_code)
        self.persist.add_pattern.assert_called_once()
        pattern = self.persist.add_pattern.call_args[0][0]
        self.assertEqual("my pattern/test", pattern.pattern)

    def test_add_duplicate_returns_409(self):
        self.persist.patterns = {AutoQueuePattern("existing")}

        response = self.handler._AutoQueueHandler__handle_add_autoqueue(
            quote("existing")
        )

        self.assertEqual(409, response.status_code)

    def test_add_invalid_pattern_returns_400(self):
        self.persist.patterns = set()
        self.persist.add_pattern.side_effect = ValueError("Blank")

        response = self.handler._AutoQueueHandler__handle_add_autoqueue(quote("  "))

        self.assertEqual(400, response.status_code)


class TestAutoQueueHandlerRemove(unittest.TestCase):
    def setUp(self):
        self.persist = MagicMock(spec=AutoQueuePersist)
        self.handler = AutoQueueHandler(self.persist)

    def test_remove_existing_pattern_returns_200(self):
        self.persist.patterns = {AutoQueuePattern("existing")}

        response = self.handler._AutoQueueHandler__handle_remove_autoqueue(
            quote("existing")
        )

        self.assertEqual(200, response.status_code)
        self.persist.remove_pattern.assert_called_once()

    def test_remove_missing_pattern_returns_404(self):
        self.persist.patterns = set()

        response = self.handler._AutoQueueHandler__handle_remove_autoqueue(
            quote("missing")
        )

        self.assertEqual(404, response.status_code)
