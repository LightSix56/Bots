"""Тесты постоянного состояния: совместимость форматов, атомарность, TTL."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import storage


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "seen.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_gives_empty_state(self):
        state = storage.load_state(self.path)
        self.assertEqual(state, {"seen": {}, "alerts": {}})

    def test_corrupt_file_does_not_crash(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ это не json")
        self.assertEqual(storage.load_state(self.path)["seen"], {})

    def test_roundtrip(self):
        state = storage.empty_state()
        storage.add_seen(state["seen"], ["a", "b"])
        storage.record_alert(state["alerts"], "feed:РБК")
        storage.save_state_atomic(self.path, state)

        loaded = storage.load_state(self.path)
        self.assertEqual(set(loaded["seen"]), {"a", "b"})
        self.assertIn("feed:РБК", loaded["alerts"])

    def test_reads_legacy_flat_list_format(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(["old1", "old2"], handle)
        state = storage.load_state(self.path)
        self.assertEqual(set(state["seen"]), {"old1", "old2"})
        self.assertIsNotNone(storage.parse_iso(state["seen"]["old1"]))

    def test_save_is_atomic_leaving_no_temp_files(self):
        state = storage.empty_state()
        storage.save_state_atomic(self.path, state)
        leftovers = [n for n in os.listdir(self.tmp.name) if n.startswith(".state-")]
        self.assertEqual(leftovers, [])

    def test_save_creates_missing_directory(self):
        nested = os.path.join(self.tmp.name, "state", "seen.json")
        storage.save_state_atomic(nested, storage.empty_state())
        self.assertTrue(os.path.exists(nested))


class PruneTests(unittest.TestCase):
    def test_drops_only_expired_entries(self):
        now = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        seen = {
            "fresh": storage.to_iso(now - timedelta(days=1)),
            "borderline": storage.to_iso(now - timedelta(days=90)),
            "stale": storage.to_iso(now - timedelta(days=400)),
            "unknown": "не дата",
        }
        kept = storage.prune_seen(seen, days=90, now=now)
        self.assertEqual(set(kept), {"fresh", "borderline", "unknown"})

    def test_unlimited_when_days_zero_is_not_supported(self):
        now = datetime(2026, 1, 31, tzinfo=timezone.utc)
        seen = {"old": storage.to_iso(now - timedelta(days=5000))}
        self.assertEqual(storage.prune_seen(seen, days=1, now=now), {})


class AlertCooldownTests(unittest.TestCase):
    def test_first_alert_allowed(self):
        self.assertTrue(storage.should_alert({}, "feed", cooldown_hours=6))

    def test_repeat_suppressed_within_cooldown(self):
        now = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        alerts = {}
        storage.record_alert(alerts, "feed", now=now)
        self.assertFalse(
            storage.should_alert(alerts, "feed", cooldown_hours=6, now=now + timedelta(hours=1))
        )

    def test_repeat_allowed_after_cooldown(self):
        now = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        alerts = {}
        storage.record_alert(alerts, "feed", now=now)
        self.assertTrue(
            storage.should_alert(alerts, "feed", cooldown_hours=6, now=now + timedelta(hours=7))
        )

    def test_unknown_timestamp_allows_alert(self):
        self.assertTrue(storage.should_alert({"feed": "мусор"}, "feed", cooldown_hours=6))


class ParseIsoTests(unittest.TestCase):
    def test_parses_z_suffix(self):
        self.assertIsNotNone(storage.parse_iso("2026-01-31T12:00:00Z"))

    def test_parses_naive_as_utc(self):
        parsed = storage.parse_iso("2026-01-31T12:00:00")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_returns_none_on_garbage(self):
        self.assertIsNone(storage.parse_iso("вчера"))


if __name__ == "__main__":
    unittest.main()
