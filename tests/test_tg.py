"""Тесты транспорта Telegram: нарезка сообщений, повторы, откат с HTML."""

import unittest

import tg


class RecordingPoster:
    """Фейковый HTTP-слой: записывает вызовы и отдаёт заранее заданные ответы."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append({"url": url, "payload": dict(payload), "timeout": timeout})
        if self.responses:
            return self.responses.pop(0)
        return {"ok": True}


class SplitTests(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(tg.split_message("привет"), ["привет"])

    def test_empty_text_no_chunks(self):
        self.assertEqual(tg.split_message(""), [])

    def test_roundtrip_join_restores_text(self):
        text = "\n".join(f"строка номер {i} " + "x" * 50 for i in range(200))
        chunks = tg.split_message(text, limit=1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertEqual("\n".join(chunks), text)

    def test_splits_on_line_boundaries(self):
        chunks = tg.split_message("aaa\nbbb\nccc", limit=7)
        self.assertEqual(chunks, ["aaa\nbbb", "ccc"])

    def test_single_line_longer_than_limit_is_hard_split(self):
        chunks = tg.split_message("y" * 250, limit=100)
        self.assertEqual([len(c) for c in chunks], [100, 100, 50])
        self.assertEqual("".join(chunks), "y" * 250)

    def test_never_splits_html_entity(self):
        line = "a" * 95 + "&amp;" + "b" * 50
        chunks = tg.split_message(line, limit=100)
        for chunk in chunks:
            self.assertNotRegex(chunk, r"&[A-Za-z#0-9]{0,8}$")
        self.assertEqual("".join(chunks), line)

    def test_rejects_non_positive_limit(self):
        with self.assertRaises(ValueError):
            tg.split_message("x", limit=0)


class StripHtmlTests(unittest.TestCase):
    def test_removes_tags_and_unescapes(self):
        self.assertEqual(tg.strip_html("<b>a &amp; b</b>"), "a & b")


class SendMessageTests(unittest.TestCase):
    def test_successful_send_returns_true(self):
        poster = RecordingPoster()
        ok = tg.send_message("привет", "tok", "chat", poster=poster)
        self.assertTrue(ok)
        self.assertEqual(len(poster.calls), 1)
        self.assertEqual(poster.calls[0]["payload"]["parse_mode"], "HTML")

    def test_missing_credentials_raise(self):
        with self.assertRaises(ValueError):
            tg.send_message("x", None, "chat", poster=RecordingPoster())
        with self.assertRaises(ValueError):
            tg.send_message("x", "tok", None, poster=RecordingPoster())

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            tg.send_message("   ", "tok", "chat", poster=RecordingPoster())

    def test_html_rejection_falls_back_to_plain_text(self):
        poster = RecordingPoster([
            {"ok": False, "error_code": 400, "description": "can't parse entities"},
            {"ok": True},
        ])
        ok = tg.send_message("<b>важное</b>", "tok", "chat", poster=poster)
        self.assertTrue(ok)
        self.assertEqual(poster.calls[0]["payload"]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", poster.calls[1]["payload"])
        self.assertEqual(poster.calls[1]["payload"]["text"], "важное")

    def test_429_is_retried_after_retry_after(self):
        slept = []
        poster = RecordingPoster([
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}},
            {"ok": True},
        ])
        ok = tg.send_message("x", "tok", "chat", poster=poster, sleep=slept.append)
        self.assertTrue(ok)
        self.assertEqual(slept, [7.0])

    def test_server_error_is_retried(self):
        slept = []
        poster = RecordingPoster([{"ok": False, "error_code": 502}, {"ok": True}])
        ok = tg.send_message("x", "tok", "chat", poster=poster, sleep=slept.append)
        self.assertTrue(ok)
        self.assertEqual(len(poster.calls), 2)
        self.assertEqual(len(slept), 1)

    def test_network_exception_returns_false_without_duplicate_retry(self):
        """Потерянный ответ = неизвестно, дошло ли сообщение.

        Telegram не поддерживает ключи идемпотентности, поэтому повтор может
        создать дубль. Безопаснее честно вернуть False: состояние не сохранится,
        и попытка повторится в следующий запуск.
        """

        class FlakyPoster(RecordingPoster):
            def __call__(self, url, payload, timeout):
                self.calls.append({"payload": dict(payload)})
                raise OSError("connection reset")

        poster = FlakyPoster()
        ok = tg.send_message("x", "tok", "chat", poster=poster, sleep=lambda _s: None)
        self.assertFalse(ok)
        self.assertEqual(len(poster.calls), 1)

    def test_malformed_json_response_is_not_retried(self):
        class BadJsonPoster(RecordingPoster):
            def __call__(self, url, payload, timeout):
                self.calls.append({"payload": dict(payload)})
                return None  # _default_poster вернул None

        poster = BadJsonPoster()
        self.assertFalse(tg.send_message("x", "tok", "chat", poster=poster,
                                         sleep=lambda _s: None))
        self.assertEqual(len(poster.calls), 1)

    def test_permanent_error_returns_false(self):
        poster = RecordingPoster([{"ok": False, "error_code": 403, "description": "bot blocked"}])
        self.assertFalse(tg.send_message("x", "tok", "chat", poster=poster))
        self.assertEqual(len(poster.calls), 1)

    def test_all_attempts_exhausted_returns_false(self):
        poster = RecordingPoster([{"ok": False, "error_code": 500}] * 3)
        ok = tg.send_message("x", "tok", "chat", poster=poster, sleep=lambda _s: None)
        self.assertFalse(ok)
        self.assertEqual(len(poster.calls), 3)

    def test_exhausted_429_returns_false(self):
        poster = RecordingPoster([
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
        ])
        ok = tg.send_message(
            "x", "tok", "chat", poster=poster, max_attempts=2, sleep=lambda _s: None
        )
        self.assertFalse(ok)

    def test_partial_delivery_returns_false(self):
        text = "\n".join(f"строка {i} " + "z" * 80 for i in range(100))
        poster = RecordingPoster([
            {"ok": True},
            {"ok": False, "error_code": 403, "description": "blocked"},
        ])
        ok = tg.send_message(text, "tok", "chat", poster=poster, max_attempts=1)
        self.assertFalse(ok)
        self.assertGreater(len(poster.calls), 1)

    def test_no_parse_mode_payload_when_disabled(self):
        poster = RecordingPoster()
        tg.send_message("x", "tok", "chat", parse_mode=None, poster=poster)
        self.assertNotIn("parse_mode", poster.calls[0]["payload"])

    def test_chunks_respect_telegram_limit(self):
        text = "\n".join("w" * 300 for _ in range(40))
        poster = RecordingPoster()
        tg.send_message(text, "tok", "chat", poster=poster)
        self.assertTrue(all(len(c["payload"]["text"]) <= tg.MAX_MESSAGE_LEN for c in poster.calls))


if __name__ == "__main__":
    unittest.main()
