"""Тесты переноса истории старого формата в новое состояние."""

import json
import os
import tempfile
import unittest
from datetime import timedelta

import news
import storage
from tests.test_news import NOW, feed


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.legacy = os.path.join(self.tmp.name, "seen.json")
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.sent = []
        self.feeds = [{"source": "РБК", "url": "https://feed/rbc"}]
        self.payloads = {
            "https://feed/rbc": feed("РБК", [
                {"title": "новая", "link": "https://e.com/new", "description": "d",
                 "pubdate": "Sat, 31 Jan 2026 11:30:00 +0300"},
                {"title": "старая", "link": "https://e.com/old", "description": "d",
                 "pubdate": "Sat, 31 Jan 2026 11:00:00 +0300"},
            ])
        }

    def tearDown(self):
        self.tmp.cleanup()

    def http_get(self, url, headers=None, timeout=None):
        return self.payloads[url]

    def run_bot(self, **kwargs):
        kwargs.setdefault("feeds", self.feeds)
        kwargs.setdefault("http_get", self.http_get)
        kwargs.setdefault("send", lambda text: self.sent.append(text) or True)
        kwargs.setdefault("state_path", self.state_path)
        return news.run(now=NOW, **kwargs)

    def test_legacy_list_is_migrated(self):
        old_hash = news.parse_feed(
            feed("РБК", [{"title": "старая", "link": "https://e.com/old", "description": "d"}]),
            "РБК", now=NOW,
        )[0]["hash"]
        with open(self.legacy, "w", encoding="utf-8") as handle:
            json.dump([old_hash], handle)

        original = news.LEGACY_SEEN_PATH
        news.LEGACY_SEEN_PATH = self.legacy
        try:
            self.run_bot()
        finally:
            news.LEGACY_SEEN_PATH = original

        self.assertEqual(len(self.sent), 1)
        self.assertIn("новая", self.sent[0])
        self.assertNotIn("старая", self.sent[0])

    def test_explicit_state_is_not_overwritten_by_legacy(self):
        with open(self.legacy, "w", encoding="utf-8") as handle:
            json.dump(["чужой-хэш"], handle)
        original = news.LEGACY_SEEN_PATH
        news.LEGACY_SEEN_PATH = self.legacy
        try:
            self.run_bot(state=storage.empty_state())
        finally:
            news.LEGACY_SEEN_PATH = original
        self.assertEqual(len(self.sent), 1)

    def test_missing_legacy_file_is_not_an_error(self):
        original = news.LEGACY_SEEN_PATH
        news.LEGACY_SEEN_PATH = os.path.join(self.tmp.name, "нет-файла.json")
        try:
            self.assertTrue(self.run_bot())
        finally:
            news.LEGACY_SEEN_PATH = original

    def test_legacy_loader_reads_hashes(self):
        with open(self.legacy, "w", encoding="utf-8") as handle:
            json.dump(["a", "b"], handle)
        self.assertEqual(set(news.load_legacy_seen(self.legacy)), {"a", "b"})

    def test_legacy_loader_ignores_missing_file(self):
        self.assertEqual(news.load_legacy_seen(os.path.join(self.tmp.name, "nope.json")), {})


if __name__ == "__main__":
    unittest.main()
