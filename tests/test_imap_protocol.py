"""Проверка разбора ответа IMAP на реальных формах данных imaplib.

Фейковый ящик в test_bot.py отдаёт упрощённый ответ, а настоящий imaplib
возвращает разные структуры для FETCH FLAGS. Эти тесты фиксируют те формы,
которые реально приходят от сервера.
"""

import unittest

import bot


class FakeConn:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def fetch(self, msg_id, query):
        self.calls.append((msg_id, query))
        return self.response


class IsSeenTests(unittest.TestCase):
    def mailbox_with(self, response):
        mailbox = bot.ImapMailbox.__new__(bot.ImapMailbox)
        mailbox._conn = FakeConn(response)
        return mailbox

    def test_flags_in_bytes(self):
        mailbox = self.mailbox_with(("OK", [b"1 (FLAGS (\\Seen))"]))
        self.assertTrue(mailbox.is_seen("1"))

    def test_flags_in_tuple_pair(self):
        mailbox = self.mailbox_with(("OK", [(b"1 (FLAGS (\\Seen))", b"")]))
        self.assertTrue(mailbox.is_seen("1"))

    def test_no_seen_flag(self):
        mailbox = self.mailbox_with(("OK", [b"1 (FLAGS ())"]))
        self.assertFalse(mailbox.is_seen("1"))

    def test_other_flags_only(self):
        mailbox = self.mailbox_with(("OK", [b"1 (FLAGS (\\Answered \\Flagged))"]))
        self.assertFalse(mailbox.is_seen("1"))

    def test_seen_among_multiple_flags(self):
        mailbox = self.mailbox_with(("OK", [b"1 (FLAGS (\\Answered \\Seen))"]))
        self.assertTrue(mailbox.is_seen("1"))

    def test_seen_with_tuple_metadata_first(self):
        mailbox = self.mailbox_with(("OK", [(b"1 (FLAGS (\\Seen))", b"extra")]))
        self.assertTrue(mailbox.is_seen("1"))

    def test_uses_flags_query_not_body(self):
        """Body (BODY.PEEK[]) снял бы флаг \\Seen самим фактом чтения."""
        mailbox = self.mailbox_with(("OK", [b"1 (FLAGS ())"]))
        mailbox.is_seen("7")
        self.assertEqual(mailbox._conn.calls, [("7", "(FLAGS)")])

    def test_bad_status_raises(self):
        mailbox = self.mailbox_with(("NO", [b"error"]))
        with self.assertRaises(IOError):
            mailbox.is_seen("1")

    def test_empty_data_is_not_seen(self):
        mailbox = self.mailbox_with(("OK", [None]))
        self.assertFalse(mailbox.is_seen("1"))


class FetchRawTests(unittest.TestCase):
    def mailbox_with(self, response):
        mailbox = bot.ImapMailbox.__new__(bot.ImapMailbox)
        mailbox._conn = FakeConn(response)
        return mailbox

    def test_extracts_body_from_tuple(self):
        mailbox = self.mailbox_with(("OK", [(b"1 (BODY[] {5}", b"hello")]))
        self.assertEqual(mailbox.fetch_raw("1"), b"hello")

    def test_uses_peek_query(self):
        mailbox = self.mailbox_with(("OK", [(b"meta", b"body")]))
        mailbox.fetch_raw("3")
        self.assertEqual(mailbox._conn.calls, [("3", "(BODY.PEEK[])")])

    def test_bad_status_raises(self):
        mailbox = self.mailbox_with(("NO", []))
        with self.assertRaises(IOError):
            mailbox.fetch_raw("1")

    def test_no_tuple_raises(self):
        mailbox = self.mailbox_with(("OK", [b"1 (BODY[] {5}"]))
        with self.assertRaises(IOError):
            mailbox.fetch_raw("1")


class ConnectionTests(unittest.TestCase):
    def test_timeout_is_passed_to_ssl_constructor(self):
        """Без таймаута зависший сервер держит job до 6 часов."""
        captured = {}

        def fake_ssl(host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout
            return FakeConn(("OK", []))

        original = bot.imaplib.IMAP4_SSL
        bot.imaplib.IMAP4_SSL = fake_ssl
        try:
            bot.ImapMailbox()
        finally:
            bot.imaplib.IMAP4_SSL = original

        self.assertEqual(captured["host"], "imap.mail.ru")
        self.assertEqual(captured["port"], 993)
        self.assertEqual(captured["timeout"], bot.IMAP_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
