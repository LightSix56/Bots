"""Тесты новостного бота: разбор лент, свежесть, дедупликация, доставка-зависимое состояние."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import news
import storage

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 1, 31, 12, 0, tzinfo=MSK)

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{source}</title>{items}</channel></rss>"""

ITEM_TEMPLATE = """<item>
<title>{title}</title>
<link>{link}</link>
<description>{description}</description>
{pubdate}
</item>"""


def build_rss(source, items):
    """items: список словарей title/link/description/pubdate."""
    rendered = "".join(
        ITEM_TEMPLATE.format(
            title=item.get("title", ""),
            link=item.get("link", ""),
            description=item.get("description", ""),
            pubdate=f"<pubDate>{item['pubdate']}</pubDate>" if item.get("pubdate") else "",
        )
        for item in items
    )
    return RSS_TEMPLATE.format(source=source, items=rendered).encode("utf-8")


def feed(source, items):
    return build_rss(source, items)


class CanonicalUrlTests(unittest.TestCase):
    def test_strips_utm_parameters(self):
        url = "https://example.com/a?utm_source=tg&utm_medium=s&id=5"
        self.assertEqual(news.canonicalize_url(url), "https://example.com/a?id=5")

    def test_strips_yclid_gclid_fbclid(self):
        url = "https://example.com/a?yclid=1&gclid=2&fbclid=3&keep=4"
        self.assertEqual(news.canonicalize_url(url), "https://example.com/a?keep=4")

    def test_removes_fragment(self):
        self.assertEqual(news.canonicalize_url("https://e.com/a#part"), "https://e.com/a")

    def test_keeps_url_without_query(self):
        self.assertEqual(news.canonicalize_url("https://e.com/a"), "https://e.com/a")

    def test_survives_garbage(self):
        self.assertEqual(news.canonicalize_url("не url"), "не url")


class CleanSnippetTests(unittest.TestCase):
    def test_strips_html_and_unescapes(self):
        self.assertEqual(news.clean_snippet("<p>a &amp; b</p>"), "a & b")

    def test_collapses_whitespace(self):
        self.assertEqual(news.clean_snippet("a\n\n   b"), "a b")

    def test_truncates_on_word_boundary_with_ellipsis(self):
        text = " ".join(["слово"] * 100)
        result = news.clean_snippet(text)
        self.assertLessEqual(len(result), news.MAX_DESC_LEN + 3)
        self.assertTrue(result.endswith("..."))

    def test_short_text_untouched(self):
        self.assertEqual(news.clean_snippet("коротко"), "коротко")

    def test_empty_input(self):
        self.assertEqual(news.clean_snippet(""), "")
        self.assertEqual(news.clean_snippet(None), "")


class ParseFeedTests(unittest.TestCase):
    def test_extracts_items(self):
        raw = feed("РБК", [{"title": "Заголовок", "link": "https://e.com/1",
                            "description": "Описание"}])
        items = news.parse_feed(raw, "РБК", now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "РБК")
        self.assertEqual(items[0]["title"], "Заголовок")
        self.assertEqual(items[0]["link"], "https://e.com/1")
        self.assertEqual(items[0]["snippet"], "Описание")

    def test_skips_items_without_title_or_link(self):
        raw = feed("X", [
            {"title": "", "link": "https://e.com/1"},
            {"title": "Есть", "link": ""},
            {"title": "Валидный", "link": "https://e.com/2"},
        ])
        items = news.parse_feed(raw, "X", now=NOW)
        self.assertEqual([i["title"] for i in items], ["Валидный"])

    def test_snippet_equal_to_title_is_dropped(self):
        raw = feed("X", [{"title": "Одно и то же", "link": "https://e.com/1",
                          "description": "Одно и то же"}])
        self.assertEqual(news.parse_feed(raw, "X", now=NOW)[0]["snippet"], "")

    def test_hash_is_stable_across_utm_variants(self):
        first = news.parse_feed(
            feed("X", [{"title": "T", "link": "https://e.com/1?utm_source=a"}]), "X", now=NOW
        )
        second = news.parse_feed(
            feed("X", [{"title": "T", "link": "https://e.com/1?utm_source=b"}]), "X", now=NOW
        )
        self.assertEqual(first[0]["hash"], second[0]["hash"])

    def test_parses_publication_date(self):
        raw = feed("X", [{"title": "T", "link": "https://e.com/1",
                          "pubdate": "Wed, 21 Jan 2026 09:00:00 +0300"}])
        published = news.parse_feed(raw, "X", now=NOW)[0]["published"]
        self.assertEqual(published, datetime(2026, 1, 21, 6, 0, tzinfo=timezone.utc))

    def test_missing_date_gives_none(self):
        raw = feed("X", [{"title": "T", "link": "https://e.com/1"}])
        self.assertIsNone(news.parse_feed(raw, "X", now=NOW)[0]["published"])

    def test_broken_xml_returns_empty_list(self):
        self.assertEqual(news.parse_feed(b"<rss><channel><item", "X", now=NOW), [])

    def test_empty_bytes_returns_empty_list(self):
        self.assertEqual(news.parse_feed(b"", "X", now=NOW), [])


class FreshnessTests(unittest.TestCase):
    def item(self, key, published):
        return {"hash": key, "published": published, "source": "X", "title": key,
                "link": f"https://e.com/{key}", "snippet": ""}

    def test_fresh_excludes_seen(self):
        items = [self.item("a", NOW), self.item("b", NOW)]
        fresh = news.filter_fresh(items, {"a"})
        self.assertEqual([i["hash"] for i in fresh], ["b"])

    def test_newest_first(self):
        old = self.item("old", NOW - timedelta(days=2))
        new = self.item("new", NOW - timedelta(minutes=5))
        ordered = news.sort_by_recency([old, new])
        self.assertEqual([i["hash"] for i in ordered], ["new", "old"])

    def test_items_without_date_go_last(self):
        dated = self.item("dated", NOW - timedelta(hours=1))
        undated = self.item("undated", None)
        self.assertEqual([i["hash"] for i in news.sort_by_recency([undated, dated])],
                         ["dated", "undated"])

    def test_ancient_items_are_dropped(self):
        stale = self.item("stale", NOW - timedelta(days=30))
        kept = self.item("kept", NOW - timedelta(days=1))
        result = news.drop_stale([stale, kept], now=NOW, max_age_days=news.MAX_AGE_DAYS)
        self.assertEqual([i["hash"] for i in result], ["kept"])

    def test_undated_items_are_not_dropped_as_stale(self):
        undated = self.item("undated", None)
        result = news.drop_stale([undated], now=NOW, max_age_days=1)
        self.assertEqual(len(result), 1)


class DigestSelectionTests(unittest.TestCase):
    def item(self, source, key, minutes_ago=0):
        return {
            "hash": key, "source": source, "title": key,
            "link": f"https://e.com/{key}", "snippet": "",
            "published": NOW - timedelta(minutes=minutes_ago),
        }

    def test_round_robin_over_sources(self):
        items = news.sort_by_recency([
            self.item("РБК", "r1", 1), self.item("РБК", "r2", 2), self.item("РБК", "r3", 3),
            self.item("Хабр", "h1", 4), self.item("Хабр", "h2", 5),
        ])
        selected = news.select_digest(items, limit=4)
        self.assertEqual([i["hash"] for i in selected], ["r1", "h1", "r2", "h2"])

    def test_limit_respected(self):
        items = [self.item("A", f"a{i}", i) for i in range(10)]
        self.assertEqual(len(news.select_digest(items, limit=3)), 3)

    def test_no_repeats_in_selection(self):
        items = news.sort_by_recency([self.item("A", "a1", 1), self.item("B", "b1", 2)])
        selected = news.select_digest(items, limit=8)
        self.assertEqual(len({i["hash"] for i in selected}), len(selected))

    def test_empty_input(self):
        self.assertEqual(news.select_digest([], limit=5), [])

    def test_recency_order_preserved_across_sources(self):
        items = news.sort_by_recency([
            self.item("A", "a1", 10),
            self.item("B", "b1", 1),
        ])
        selected = news.select_digest(items, limit=2)
        self.assertEqual(selected[0]["hash"], "b1")


class MessageTests(unittest.TestCase):
    def sample(self):
        return [{
            "hash": "h", "source": "РБК", "title": "Заголовок с <тегом> & амперсандом",
            "link": "https://e.com/1?a=b&c=d", "snippet": "Описание с <b>разметкой</b>",
            "published": NOW,
        }]

    def test_escapes_html_in_title_and_snippet(self):
        text = news.format_digest(self.sample(), NOW)
        self.assertIn("&lt;тегом&gt;", text)
        self.assertIn("&amp;", text)
        self.assertNotIn("<b>разметкой</b>", text)

    def test_link_is_clickable_html(self):
        text = news.format_digest(self.sample(), NOW)
        self.assertIn('href="https://e.com/1?a=b&amp;c=d"', text)

    def test_header_uses_actual_time_not_hardcoded(self):
        """Регресс: заголовок был зашит как «на 12:00 МСК» и врал при ручном запуске."""
        text = news.format_digest(self.sample(), NOW.replace(hour=7, minute=41))
        self.assertIn("07:41", text)
        self.assertNotIn("12:00", text)

    def test_header_contains_date(self):
        self.assertIn("31.01.2026", news.format_digest(self.sample(), NOW))

    def test_failed_sources_are_reported(self):
        text = news.format_digest(self.sample(), NOW, failures=["Хабр"])
        self.assertIn("Хабр", text)

    def test_message_without_failures_has_no_warning(self):
        self.assertNotIn("Недоступны", news.format_digest(self.sample(), NOW))

    def test_empty_list_gives_placeholder(self):
        self.assertIn("Нет новых", news.format_digest([], NOW))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.sent = []
        self.feeds = [
            {"source": "РБК", "url": "https://feed/rbc"},
            {"source": "Хабр", "url": "https://feed/habr"},
        ]
        self.payloads = {
            "https://feed/rbc": feed("РБК", [
                {"title": "r1", "link": "https://e.com/r1", "description": "d",
                 "pubdate": "Sat, 31 Jan 2026 11:30:00 +0300"},
                {"title": "r2", "link": "https://e.com/r2", "description": "d",
                 "pubdate": "Sat, 31 Jan 2026 11:00:00 +0300"},
            ]),
            "https://feed/habr": feed("Хабр", [
                {"title": "h1", "link": "https://e.com/h1", "description": "d",
                 "pubdate": "Sat, 31 Jan 2026 11:45:00 +0300"},
            ]),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def http_get(self, url, headers=None, timeout=None):
        if url not in self.payloads:
            raise OSError(f"unknown feed {url}")
        return self.payloads[url]

    def run_bot(self, **kwargs):
        kwargs.setdefault("send", lambda text: self.sent.append(text) or True)
        kwargs.setdefault("state", storage.empty_state())
        kwargs.setdefault("state_path", self.state_path)
        kwargs.setdefault("http_get", self.http_get)
        kwargs.setdefault("feeds", self.feeds)
        return news.run(now=NOW, **kwargs)

    def test_sends_digest_with_items_from_both_sources(self):
        self.assertTrue(self.run_bot())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("r1", self.sent[0])
        self.assertIn("h1", self.sent[0])

    def test_newest_item_first(self):
        self.run_bot()
        self.assertLess(self.sent[0].index("h1"), self.sent[0].index("r1"))

    def test_seen_is_saved_after_successful_delivery(self):
        state = storage.empty_state()
        self.run_bot(state=state)
        saved = storage.load_state(self.state_path)
        self.assertEqual(len(saved["seen"]), 3)
        self.assertEqual(set(saved["seen"]), set(state["seen"]))

    def test_seen_not_saved_when_delivery_fails(self):
        """Регресс: раньше хэши сохранялись даже при сбое Telegram — новость терялась."""
        self.run_bot(send=lambda _text: False)
        self.assertEqual(storage.load_state(self.state_path)["seen"], {})

    def test_second_run_skips_already_sent(self):
        state = storage.empty_state()
        self.run_bot(state=state)
        self.sent.clear()
        self.run_bot(state=state)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(storage.load_state(self.state_path)["seen"]), 3)

    def test_seen_not_saved_when_nothing_to_send(self):
        state = storage.empty_state()
        self.run_bot(state=state)
        first = storage.load_state(self.state_path)["seen"]
        self.run_bot(state=state)
        self.assertEqual(storage.load_state(self.state_path)["seen"], first)

    def test_one_feed_failure_still_delivers_others(self):
        def flaky(url, headers=None, timeout=None):
            if "rbc" in url:
                raise OSError("timeout")
            return self.payloads[url]

        self.assertTrue(self.run_bot(http_get=flaky))
        self.assertIn("h1", self.sent[0])
        self.assertIn("РБК", self.sent[0])  # источник указан как недоступный

    def test_all_feeds_failing_sends_alert_and_returns_false(self):
        def dead(url, headers=None, timeout=None):
            raise OSError("dns")

        alerts = []
        ok = self.run_bot(http_get=dead, send_alert=lambda text: alerts.append(text) or True)
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(alerts), 1)

    def test_alert_cooldown_suppresses_repeats(self):
        def dead(url, headers=None, timeout=None):
            raise OSError("dns")

        alerts = []
        state = storage.empty_state()
        shared = dict(http_get=dead, send_alert=lambda text: alerts.append(text) or True,
                      state=state)
        self.run_bot(**shared)
        self.run_bot(**shared)
        self.assertEqual(len(alerts), 1)

    def test_check_mode_prints_without_sending(self):
        self.assertTrue(self.run_bot(check_mode=True))
        self.assertEqual(self.sent, [])

    def test_state_written_atomically(self):
        self.run_bot()
        leftovers = [n for n in os.listdir(self.tmp.name) if n.startswith(".state-")]
        self.assertEqual(leftovers, [])

    def test_timeout_is_passed_to_http(self):
        seen_timeout = {}

        def recorder(url, headers=None, timeout=None):
            seen_timeout[url] = timeout
            return self.payloads[url]

        self.run_bot(http_get=recorder)
        self.assertTrue(all(t == news.HTTP_TIMEOUT for t in seen_timeout.values()))

    def test_missing_credentials_do_not_crash(self):
        """Без токена run обязан вернуть False, а не упасть трейсбеком."""
        os.environ.pop("NEWS_TG_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_TOKEN", None)
        os.environ.pop("TG_BOT_TOKEN", None)
        os.environ.pop("TG_CHAT_ID", None)
        self.assertFalse(self.run_bot(send=None, check_mode=False))


if __name__ == "__main__":
    unittest.main()
