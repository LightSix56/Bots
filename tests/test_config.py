"""Тесты чтения конфигурации из окружения."""

import unittest

import config


class FirstSetTests(unittest.TestCase):
    def test_returns_first_non_empty(self):
        env = {"A": "", "B": "  ", "C": "value"}
        self.assertEqual(config.first_set(env, ["A", "B", "C"]), "value")

    def test_strips_whitespace(self):
        self.assertEqual(config.first_set({"A": " tok "}, ["A"]), "tok")

    def test_none_when_nothing_set(self):
        self.assertIsNone(config.first_set({}, ["A", "B"]))


class TelegramCredentialsTests(unittest.TestCase):
    def test_reads_dedicated_token_and_chat(self):
        env = {"WEATHER_TG_BOT_TOKEN": "w", "TG_CHAT_ID": "42"}
        self.assertEqual(
            config.telegram_credentials(["WEATHER_TG_BOT_TOKEN"], env=env), ("w", "42")
        )

    def test_dedicated_token_wins_over_legacy(self):
        env = {"NEWS_TG_BOT_TOKEN": "new", "TELEGRAM_TOKEN": "old", "TG_CHAT_ID": "1"}
        token, _ = config.telegram_credentials(["NEWS_TG_BOT_TOKEN"], env=env)
        self.assertEqual(token, "new")

    def test_legacy_token_alias_supported(self):
        env = {"TG_BOT_TOKEN": "legacy", "TG_CHAT_ID": "1"}
        token, _ = config.telegram_credentials(["MAIL_TG_BOT_TOKEN"], env=env)
        self.assertEqual(token, "legacy")

    def test_missing_chat_id_raises(self):
        env = {"MAIL_TG_BOT_TOKEN": "t"}
        with self.assertRaises(config.ConfigError):
            config.telegram_credentials(["MAIL_TG_BOT_TOKEN"], env=env)

    def test_missing_token_raises_with_variable_names(self):
        env = {"TG_CHAT_ID": "1"}
        with self.assertRaises(config.ConfigError) as ctx:
            config.telegram_credentials(["MAIL_TG_BOT_TOKEN"], env=env)
        self.assertIn("MAIL_TG_BOT_TOKEN", str(ctx.exception))

    def test_no_implicit_chat_id_fallback(self):
        """Запасного chat_id быть не должно: иначе письма уйдут в чужой чат."""
        env = {"MAIL_TG_BOT_TOKEN": "t"}
        self.assertFalse(config.flag("CHECK_MODE", env={}))
        with self.assertRaises(config.ConfigError):
            config.telegram_credentials(["MAIL_TG_BOT_TOKEN"], env=env)

    def test_require_false_tolerates_missing(self):
        token, chat_id = config.telegram_credentials([], env={}, require=False)
        self.assertIsNone(token)
        self.assertIsNone(chat_id)


class FlagTests(unittest.TestCase):
    def test_truthy_values(self):
        for value in ["1", "true", "TRUE", "yes", "on", "да"]:
            self.assertTrue(config.flag("X", env={"X": value}), value)

    def test_falsy_values(self):
        for value in ["0", "false", "no", "", "off"]:
            self.assertFalse(config.flag("X", env={"X": value}), value)

    def test_default_used_when_unset(self):
        self.assertTrue(config.flag("X", env={}, default=True))
        self.assertFalse(config.flag("X", env={}, default=False))


if __name__ == "__main__":
    unittest.main()
