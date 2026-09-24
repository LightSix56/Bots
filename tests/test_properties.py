"""Граничные свойства функций выборки дайджеста и нарезки сообщений.

Проверяются инварианты, а не отдельные примеры: на больших и неоднородных
входах функции обязаны сохранять длину, состав и завершаться.
"""

import random
import unittest
from datetime import datetime, timedelta, timezone

import news
import tg

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 1, 31, 12, 0, tzinfo=MSK)


def random_items(count, sources):
    items = []
    for index in range(count):
        items.append({
            "hash": f"h{index}",
            "source": random.choice(sources),
            "title": f"t{index}",
            "link": f"https://e.com/{index}",
            "snippet": "",
            "published": NOW - timedelta(minutes=random.randint(0, 10000)),
        })
    return items


class SelectDigestPropertyTests(unittest.TestCase):
    def test_never_exceeds_limit_on_random_input(self):
        random.seed(1234)
        for _ in range(200):
            items = random_items(random.randint(0, 60), ["A", "B", "C", "D", "E"])
            limit = random.randint(1, 12)
            selected = news.select_digest(items, limit=limit)
            self.assertLessEqual(len(selected), limit)

    def test_terminates_when_fewer_items_than_limit(self):
        random.seed(99)
        for _ in range(100):
            items = random_items(random.randint(0, 5), ["A", "B"])
            selected = news.select_digest(items, limit=50)
            self.assertEqual(len(selected), len(items))

    def test_no_duplicates_selected(self):
        random.seed(7)
        for _ in range(100):
            items = random_items(random.randint(0, 40), ["A", "B", "C"])
            selected = news.select_digest(items, limit=8)
            hashes = [item["hash"] for item in selected]
            self.assertEqual(len(hashes), len(set(hashes)))

    def test_single_source_keeps_order(self):
        random.seed(3)
        items = news.sort_by_recency(random_items(20, ["A"]))
        selected = news.select_digest(items, limit=5)
        self.assertEqual(selected, items[:5])

    def test_balanced_across_sources_when_possible(self):
        items = news.sort_by_recency(
            [dict(hash=f"a{i}", source="A", title="", link="", snippet="",
                  published=NOW - timedelta(minutes=i)) for i in range(5)]
            + [dict(hash=f"b{i}", source="B", title="", link="", snippet="",
                    published=NOW - timedelta(minutes=i)) for i in range(5)]
        )
        selected = news.select_digest(items, limit=6)
        self.assertEqual(len([i for i in selected if i["source"] == "A"]), 3)
        self.assertEqual(len([i for i in selected if i["source"] == "B"]), 3)

    def test_zero_or_negative_limit(self):
        items = random_items(5, ["A"])
        self.assertEqual(news.select_digest(items, limit=0), [])
        self.assertEqual(news.select_digest(items, limit=-3), [])


class SplitMessagePropertyTests(unittest.TestCase):
    def test_no_characters_are_lost(self):
        """Части по порядку содержат весь текст, без пропусков и перестановок."""
        random.seed(42)
        alphabet = "абвгдеёжзийклмнопр &;<>\"'x"
        for _ in range(300):
            text = "".join(random.choice(alphabet) for _ in range(random.randint(0, 5000)))
            for limit in (50, 100, 1000, tg.SAFE_MESSAGE_LEN):
                chunks = tg.split_message(text, limit=limit)
                stripped = "".join(chunks).replace("\n", "")
                self.assertEqual(stripped, text.replace("\n", ""))

    def test_join_is_exact_when_no_line_exceeds_limit(self):
        """Если отдельных длинных строк нет, обратная сборка точна."""
        random.seed(8)
        for _ in range(200):
            text = "\n".join(
                "".join(random.choice("abc &;") for _ in range(random.randint(0, 40)))
                for _ in range(random.randint(0, 120))
            )
            limit = 200
            chunks = tg.split_message(text, limit=limit)
            self.assertEqual("\n".join(chunks), text)

    def test_no_chunk_exceeds_limit(self):
        random.seed(5)
        for _ in range(200):
            text = "".join(random.choice("abcdef &;") for _ in range(random.randint(0, 3000)))
            limit = random.randint(10, 500)
            for chunk in tg.split_message(text, limit=limit):
                self.assertLessEqual(len(chunk), limit)

    def test_entities_are_never_split_across_chunks(self):
        """Ни одна HTML-сущность не должна оказаться разрезанной между частями.

        Проверяются реальные границы: известно, где в исходном тексте лежат
        сущности, и ни одна граница части не должна попасть внутрь них.
        """
        random.seed(11)
        entities = ["&amp;", "&#33;", "&lt;", "&gt;"]
        for _ in range(200):
            parts = []
            positions = []
            total = 0
            for _ in range(random.randint(1, 40)):
                filler = "".join(random.choice("abxy ") for _ in range(random.randint(0, 15)))
                parts.append(filler)
                total += len(filler)
                entity = random.choice(entities)
                positions.append((total, total + len(entity)))
                parts.append(entity)
                total += len(entity)
            text = "".join(parts)

            limit = random.randint(10, 60)
            chunks = tg.split_message(text, limit=limit)

            # Смещения начала каждой части в исходном тексте.
            boundaries = []
            offset = 0
            for chunk in chunks[:-1]:
                offset += len(chunk)
                boundaries.append(offset)

            for start, end in positions:
                for boundary in boundaries:
                    self.assertFalse(
                        start < boundary < end,
                        f"граница {boundary} разрезает сущность {text[start:end]!r}",
                    )

    def test_ampersand_guard_moves_cut_before_entity(self):
        """Разрез не должен попадать внутрь сущности на границе лимита."""
        text = "x" * 38 + "&#33;" + "y" * 30
        chunks = tg.split_message(text, limit=40)
        self.assertEqual(chunks[0], "x" * 38)
        self.assertTrue(chunks[1].startswith("&#33;"))
        self.assertEqual("".join(chunks), text)

    def test_empty_and_single_newline(self):
        self.assertEqual(tg.split_message(""), [])
        self.assertEqual(tg.split_message("\n", limit=1), ["\n"])

    def test_long_run_without_spaces(self):
        text = "z" * 10000
        chunks = tg.split_message(text, limit=4096)
        self.assertEqual("".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
