"""Проверки workflow GitHub Actions.

Тесты текстовые, без YAML-парсера: дополнительная зависимость ради проверки
нескольких строк не нужна. Смысл — поймать расхождение между именами
переменных окружения в workflow и в коде: именно так бот молча оставался без
токена (в mail-checker.yml стояло TG_BOT_TOKEN, а bot.py читает MAIL_TG_BOT_TOKEN).
"""

import os
import re
import unittest

WORKFLOWS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "workflows")

# workflow -> переменные, которые обязан объявить (иначе бот не увидит секрет)
REQUIRED_ENV = {
    "mail-checker.yml": ["MAIL_USER", "MAIL_PASS", "MAIL_TG_BOT_TOKEN", "TG_CHAT_ID", "STATE_PATH"],
    "news.yml": ["NEWS_TG_BOT_TOKEN", "TG_CHAT_ID", "STATE_PATH"],
    "weather.yml": ["WEATHER_TG_BOT_TOKEN", "TG_CHAT_ID", "STATE_PATH"],
}

ALL_WORKFLOWS = ["mail-checker.yml", "news.yml", "weather.yml", "tests.yml"]


def read_workflow(name):
    path = os.path.join(WORKFLOWS_DIR, name)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class WorkflowFilesTests(unittest.TestCase):
    def test_all_workflows_exist(self):
        for name in ALL_WORKFLOWS:
            self.assertTrue(os.path.exists(os.path.join(WORKFLOWS_DIR, name)), name)

    def test_no_trailing_whitespace_issues_or_tabs(self):
        for name in ALL_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertNotIn("\t", read_workflow(name))


class EnvWiringTests(unittest.TestCase):
    def test_required_env_variables_present(self):
        for name, variables in REQUIRED_ENV.items():
            text = read_workflow(name)
            for variable in variables:
                with self.subTest(workflow=name, variable=variable):
                    self.assertIn(f"{variable}:", text)

    def test_mail_bot_does_not_use_generic_token_only(self):
        """bot.py читает MAIL_TG_BOT_TOKEN; подстановка в TG_BOT_TOKEN его не кормит."""
        text = read_workflow("mail-checker.yml")
        self.assertIn("MAIL_TG_BOT_TOKEN:", text)
        self.assertNotRegex(text, r"^\s+TG_BOT_TOKEN:", )

    def test_news_uses_dedicated_token(self):
        self.assertIn("NEWS_TG_BOT_TOKEN:", read_workflow("news.yml"))

    def test_weather_uses_dedicated_token(self):
        self.assertIn("WEATHER_TG_BOT_TOKEN:", read_workflow("weather.yml"))

    def test_state_path_points_into_data_directory(self):
        for name in ("mail-checker.yml", "news.yml", "weather.yml"):
            with self.subTest(workflow=name):
                self.assertRegex(read_workflow(name), r"STATE_PATH:\s+data/")


class BudgetProtectionTests(unittest.TestCase):
    def test_every_workflow_has_timeout(self):
        for name in ALL_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertIn("timeout-minutes:", read_workflow(name))

    def test_scheduled_workflows_have_concurrency(self):
        for name in ("mail-checker.yml", "news.yml", "weather.yml"):
            with self.subTest(workflow=name):
                self.assertIn("concurrency:", read_workflow(name))

    def test_concurrency_does_not_cancel_running_job(self):
        """Отмена на середине опасна: письмо может быть уже отправлено, но не помечено."""
        for name in ("mail-checker.yml", "news.yml", "weather.yml"):
            with self.subTest(workflow=name):
                self.assertIn("cancel-in-progress: false", read_workflow(name))


class StateHandlingTests(unittest.TestCase):
    def test_scheduled_workflows_restore_and_save_state(self):
        for name in ("mail-checker.yml", "news.yml", "weather.yml"):
            with self.subTest(workflow=name):
                text = read_workflow(name)
                self.assertIn("actions/cache/restore@v4", text)
                self.assertIn("actions/cache/save@v4", text)

    def test_state_saved_even_on_failure(self):
        for name in ("mail-checker.yml", "news.yml", "weather.yml"):
            with self.subTest(workflow=name):
                text = read_workflow(name)
                save_index = text.index("actions/cache/save@v4")
                self.assertIn("if: always()", text[:save_index] + text[save_index - 200:save_index])

    def test_no_workflow_commits_state_to_git(self):
        """История состояния больше не коммитится в main."""
        for name in ALL_WORKFLOWS:
            with self.subTest(workflow=name):
                text = read_workflow(name)
                self.assertNotIn("git push", text)
                self.assertNotIn("git commit", text)


class PermissionsTests(unittest.TestCase):
    def test_workflows_do_not_request_write_permissions(self):
        """Права на запись нужны были только для коммита seen.json; теперь не нужны."""
        for name in ALL_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertNotIn("contents: write", read_workflow(name))


class TestsWorkflowTests(unittest.TestCase):
    def test_ci_runs_unit_tests(self):
        text = read_workflow("tests.yml")
        self.assertIn("unittest discover", text)

    def test_ci_triggers_on_push_and_pull_request(self):
        text = read_workflow("tests.yml")
        self.assertIn("push:", text)
        self.assertIn("pull_request:", text)

    def test_ci_installs_requirements(self):
        self.assertIn("pip install -r requirements.txt", read_workflow("tests.yml"))


if __name__ == "__main__":
    unittest.main()
