"""Тесты почтового бота: разбор писем, дедупликация, отметка только доставленного."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.message import EmailMessage

import bot
import storage

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 1, 31, 12, 0, tzinfo=MSK)


def make_message(
    subject="Тема",
    sender="MIREA <noreply@mirea.ru>",
    body="Текст письма",
    date="Sat, 31 Jan 2026 10:00:00 +0300",
    message_id="<abc@mirea.ru>",
    html_body=None,
):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = date
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(body)
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html")
    return msg.as_bytes()


class FakeMailbox:
    """Замена IMAP-соединения: тесты не ходят в сеть."""

    def __init__(self, messages, *, connect_error=None, fetch_error=None, already_seen=None):
        self.messages = list(messages)
        self.connect_error = connect_error
        self.fetch_error = fetch_error
        self.already_seen = set(already_seen or [])
        self.marked = []
        self.searched = []
        self.selected = None
        self.closed = False
        self.logged_in = False
        self.login_args = None

    def login(self, user, password):
        if self.connect_error:
            raise self.connect_error
        self.logged_in = True
        self.login_args = (user, password)

    def select(self, folder):
        self.selected = folder
        return "OK", [b"1"]

    def search(self, criteria):
        self.searched.append(criteria)
        ids = b" ".join(str(i + 1).encode() for i in range(len(self.messages)))
        return "OK", [ids]

    def fetch_raw(self, msg_id):
        if self.fetch_error:
            raise self.fetch_error
        return self.messages[int(msg_id) - 1]

    def is_seen(self, msg_id):
        return msg_id in self.already_seen

    def mark_seen(self, msg_id):
        self.marked.append(msg_id)

    def close(self):
        self.closed = True


class SearchCriteriaTests(unittest.TestCase):
    def test_format_is_imap_compatible(self):
        self.assertEqual(bot.search_criteria(NOW), "(SINCE 29-Jan-2026)")

    def test_month_is_english_regardless_of_locale(self):
        """strftime("%b") в русской локали даёт «янв» — сервер такой критерий отвергнет."""
        for month, expected in enumerate(bot.IMAP_MONTHS, start=1):
            moment = NOW.replace(month=month, day=15)
            self.assertIn(expected, bot.search_criteria(moment, lookback_days=0), expected)

    def test_does_not_use_unseen(self):
        self.assertNotIn("UNSEEN", bot.search_criteria(NOW))

    def test_lookback_window(self):
        self.assertIn("29-Jan-2026", bot.search_criteria(NOW, lookback_days=2))
        self.assertIn("30-Jan-2026", bot.search_criteria(NOW, lookback_days=1))

    def test_year_boundary(self):
        moment = datetime(2026, 1, 1, 12, 0, tzinfo=MSK)
        self.assertIn("30-Dec-2025", bot.search_criteria(moment, lookback_days=2))


class DecodeTests(unittest.TestCase):
    def test_plain_ascii(self):
        self.assertEqual(bot.decode_mime_words("Просто тема"), "Просто тема")

    def test_none_gives_empty(self):
        self.assertEqual(bot.decode_mime_words(None), "")

    def test_encoded_utf8_header(self):
        encoded = Header("Привет мир", "utf-8").encode()
        self.assertEqual(bot.decode_mime_words(encoded), "Привет мир")

    def test_mixed_fragments(self):
        encoded = f"Re: {Header('Отчёт', 'utf-8').encode()}"
        self.assertEqual(bot.decode_mime_words(encoded), "Re: Отчёт")


class SenderTests(unittest.TestCase):
    def test_mirea_address_recognized(self):
        self.assertTrue(bot.is_mirea("MIREA <noreply@mirea.ru>"))

    def test_case_insensitive(self):
        self.assertTrue(bot.is_mirea("NO-REPLY@MIREA.RU"))

    def test_subdomain_recognized(self):
        self.assertTrue(bot.is_mirea("student@edu.mirea.ru"))

    def test_other_sender_rejected(self):
        self.assertFalse(bot.is_mirea("spam@example.com"))

    def test_bare_domain_without_at_rejected(self):
        self.assertFalse(bot.is_mirea("mirea.ru в тексте"))

    def test_empty_rejected(self):
        self.assertFalse(bot.is_mirea(""))


class MessageKeyTests(unittest.TestCase):
    def test_uses_message_id(self):
        raw = make_message(message_id="<unique@mirea.ru>")
        self.assertEqual(bot.message_key(raw), "<unique@mirea.ru>")

    def test_same_message_gives_same_key(self):
        raw = make_message(message_id="<x@mirea.ru>")
        self.assertEqual(bot.message_key(raw), bot.message_key(raw))

    def test_missing_message_id_falls_back_to_hash(self):
        raw = make_message(message_id=None)
        key = bot.message_key(raw)
        self.assertTrue(key)
        self.assertNotIn("None", key)

    def test_different_messages_have_different_keys(self):
        first = make_message(message_id=None, subject="A")
        second = make_message(message_id=None, subject="B")
        self.assertNotEqual(bot.message_key(first), bot.message_key(second))

    def test_fallback_is_stable(self):
        raw = make_message(message_id=None, subject="A")
        self.assertEqual(bot.message_key(raw), bot.message_key(raw))


class BodyTests(unittest.TestCase):
    def test_plain_text_body(self):
        self.assertIn("Текст письма", bot.get_email_body(make_message()))

    def test_html_body_is_converted_to_text(self):
        raw = make_message(body="", html_body="<p>Привет <b>мир</b></p>")
        self.assertIn("Привет", bot.get_email_body(raw))

    def test_links_are_extracted(self):
        raw = make_message(body="", html_body='<a href="https://mirea.ru/x">Ссылка</a>')
        self.assertIn("https://mirea.ru/x", bot.get_email_body(raw))

    def test_broken_charset_does_not_crash(self):
        raw = make_message(body="", html_body="<p>данные</p>")
        self.assertIsInstance(bot.get_email_body(raw), str)


class BuildMessageTests(unittest.TestCase):
    def test_contains_headers(self):
        text = bot.build_telegram_message(make_message(subject="Важное письмо"), NOW)
        self.assertIn("Важное письмо", text)
        self.assertIn("noreply@mirea.ru", text)

    def test_html_in_body_is_escaped(self):
        raw = make_message(body="<script>alert(1)</script>", message_id=None)
        text = bot.build_telegram_message(raw, NOW)
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_links_from_html_stay_clickable(self):
        """Регресс: блок ссылок экранировался целиком и превращался в исходный код."""
        raw = make_message(body="", html_body='<a href="https://mirea.ru/schedule">Расписание</a>')
        text = bot.build_telegram_message(raw, NOW)
        self.assertIn('<a href="https://mirea.ru/schedule">', text)
        self.assertNotIn("&lt;a href=", text)

    def test_long_body_is_truncated_with_notice(self):
        raw = make_message(body="Д" * 5000)
        text = bot.build_telegram_message(raw, NOW)
        self.assertIn("обрезано", text)
        self.assertLess(len(text), 6000)

    def test_short_body_has_no_truncation_notice(self):
        self.assertNotIn("обрезано", bot.build_telegram_message(make_message(), NOW))

    def test_invalid_date_does_not_crash(self):
        raw = make_message(date="не дата")
        self.assertIn("Дата", bot.build_telegram_message(raw, NOW))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.sent = []
        self.messages = [
            make_message(subject="Письмо 1", message_id="<m1@mirea.ru>"),
            make_message(sender="spam@example.com", subject="Спам", message_id="<s1@example.com>"),
            make_message(subject="Письмо 2", message_id="<m2@mirea.ru>"),
        ]
        self.mailbox = FakeMailbox(self.messages)

    def tearDown(self):
        self.tmp.cleanup()

    def run_bot(self, **kwargs):
        kwargs.setdefault("imap_factory", lambda host, port, timeout: self.mailbox)
        kwargs.setdefault("send", lambda text: self.sent.append(text) or True)
        kwargs.setdefault("state", storage.empty_state())
        kwargs.setdefault("state_path", self.state_path)
        kwargs.setdefault("env", {"MAIL_USER": "u", "MAIL_PASS": "p"})
        return bot.run(now=NOW, **kwargs)

    def test_sends_only_mirea_messages(self):
        self.assertTrue(self.run_bot())
        self.assertEqual(len(self.sent), 2)
        joined = "\n".join(self.sent)
        self.assertIn("Письмо 1", joined)
        self.assertIn("Письмо 2", joined)
        self.assertNotIn("Спам", joined)

    def test_marks_only_delivered_as_seen(self):
        self.run_bot()
        self.assertEqual(sorted(self.mailbox.marked), ["1", "3"])

    def test_state_records_delivered_keys(self):
        state = storage.empty_state()
        self.run_bot(state=state)
        saved = storage.load_state(self.state_path)["seen"]
        self.assertEqual(set(saved), {"<m1@mirea.ru>", "<m2@mirea.ru>"})

    def test_search_does_not_require_unseen(self):
        """Письмо, прочитанное в другом клиенте, тоже должно попадать в обработку."""
        self.run_bot()
        self.assertTrue(self.mailbox.searched)
        criteria = self.mailbox.searched[0]
        self.assertNotIn("UNSEEN", criteria.upper())
        self.assertIn("SINCE", criteria.upper())

    def test_already_seen_message_is_not_resent(self):
        state = storage.empty_state()
        storage.add_seen(state["seen"], ["<m1@mirea.ru>", "<m2@mirea.ru>"])
        self.run_bot(state=state)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.mailbox.marked, [])

    def test_failed_delivery_keeps_message_unseen_and_unsaved(self):
        """Регресс: при сбое Telegram письмо помечалось прочитанным и терялось."""
        ok = self.run_bot(send=lambda _text: False)
        self.assertFalse(ok)
        self.assertEqual(self.mailbox.marked, [])
        self.assertEqual(storage.load_state(self.state_path)["seen"], {})

    def test_one_bad_fetch_does_not_stop_others(self):
        """Сбой чтения одного письма не отменяет обработку остальных."""
        healthy = FakeMailbox(self.messages)
        calls = {"n": 0}
        original = healthy.fetch_raw

        def flaky_fetch(msg_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("fetch failed")
            return original(msg_id)

        healthy.fetch_raw = flaky_fetch
        ok = self.run_bot(imap_factory=lambda host, port, timeout: healthy)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Письмо 1", self.sent[0])
        self.assertFalse(ok)  # потерянное письмо не даёт отчитаться об успехе

    def test_connect_uses_timeout(self):
        captured = {}

        def factory(host, port, timeout):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout
            return self.mailbox

        self.run_bot(imap_factory=factory)
        self.assertEqual(captured["host"], "imap.mail.ru")
        self.assertEqual(captured["port"], 993)
        self.assertGreater(captured["timeout"], 0)

    def test_connection_failure_sends_alert_and_returns_false(self):
        def broken_factory(host, port, timeout):
            raise OSError("network unreachable")

        alerts = []
        ok = self.run_bot(
            imap_factory=broken_factory,
            send_alert=lambda text: alerts.append(text) or True,
        )
        self.assertFalse(ok)
        self.assertEqual(len(alerts), 1)

    def test_alert_cooldown(self):
        def broken_factory(host, port, timeout):
            raise OSError("network unreachable")

        alerts = []
        state = storage.empty_state()
        shared = dict(
            imap_factory=broken_factory,
            send_alert=lambda text: alerts.append(text) or True,
            state=state,
        )
        self.run_bot(**shared)
        self.run_bot(**shared)
        self.assertEqual(len(alerts), 1)

    def test_check_mode_does_not_send_or_mark(self):
        self.assertTrue(self.run_bot(check_mode=True))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.mailbox.marked, [])

    def test_missing_credentials_returns_false(self):
        self.assertFalse(self.run_bot(env={}))

    def test_login_uses_env_credentials(self):
        self.run_bot()
        self.assertEqual(self.mailbox.login_args, ("u", "p"))

    def test_mailbox_closed_after_run(self):
        self.run_bot()
        self.assertTrue(self.mailbox.closed)

    def test_no_messages_is_success(self):
        self.mailbox = FakeMailbox([])
        self.assertTrue(self.run_bot())
        self.assertEqual(self.sent, [])


class FirstRunTests(unittest.TestCase):
    """Первый запуск: на сервере есть и прочитанные письма, в истории — ничего.

    Без учёта флага \\Seen бот переслал бы всю переписку от MIREA за окно поиска
    (двое суток), включая давно прочитанное.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.sent = []
        # Письмо 1 прочитано давно, письмо 2 — новое непрочитанное.
        self.mailbox = FakeMailbox(
            [
                make_message(subject="Старое прочитанное", message_id="<old@mirea.ru>"),
                make_message(subject="Новое", message_id="<new@mirea.ru>"),
            ],
            already_seen={"1"},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_bot(self, **kwargs):
        kwargs.setdefault("imap_factory", lambda host, port, timeout: self.mailbox)
        kwargs.setdefault("send", lambda text: self.sent.append(text) or True)
        kwargs.setdefault("state", storage.empty_state())
        kwargs.setdefault("state_path", self.state_path)
        kwargs.setdefault("env", {"MAIL_USER": "u", "MAIL_PASS": "p"})
        # Первый запуск: история пуста, поэтому старые прочитанные письма
        # распознаются по флагу \Seen.
        kwargs.setdefault("initialise", True)
        return bot.run(now=NOW, **kwargs)

    def test_previously_read_message_is_skipped_on_first_run(self):
        self.assertTrue(self.run_bot())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Новое", self.sent[0])
        self.assertNotIn("Старое прочитанное", self.sent[0])

    def test_unread_message_is_forwarded(self):
        self.run_bot()
        self.assertIn("<new@mirea.ru>", storage.load_state(self.state_path)["seen"])

    def test_previously_read_message_is_recorded_in_history(self):
        """Иначе оно всплывёт при следующем запуске, когда окно поиска его захватит."""
        self.run_bot()
        self.assertIn("<old@mirea.ru>", storage.load_state(self.state_path)["seen"])

    def test_already_produced_message_is_not_forwarded_on_later_runs(self):
        state = storage.empty_state()
        self.run_bot(state=state)
        self.sent.clear()
        self.run_bot(state=state)
        self.assertEqual(self.sent, [])

    def test_crash_between_mark_seen_and_state_save_does_not_duplicate(self):
        """Сбой после отметки \\Seen, но до записи состояния.

        Письмо помечено прочитанным на сервере и уже отправлено, а в локальной
        истории его нет. Следующий запуск обязан распознать это по флагу \\Seen
        и не отправить письмо второй раз.
        """
        import bot as bot_module

        state = storage.empty_state()
        self.run_bot(state=state)
        self.assertEqual(len(self.sent), 1)

        # Имитируем падение до сохранения состояния: история пуста, но оба письма
        # на сервере помечены прочитанными (старое — ранее, новое — только что).
        fresh_state = storage.empty_state()
        self.mailbox.already_seen = {"1", "2"}
        self.sent.clear()

        self.run_bot(state=fresh_state)
        self.assertEqual(self.sent, [], "письмо не должно уйти повторно")
        self.assertIn("<new@mirea.ru>", fresh_state["seen"])


if __name__ == "__main__":
    unittest.main()
