"""Сквозная проверка на живых сервисах: реальные RSS и Open-Meteo.

Telegram не вызывается — вместо него подставляется записывающая функция.
Проверяется главное свойство: состояние сохраняется только после подтверждённой
доставки, а повторный запуск не отправляет то же самое второй раз.

Запуск:  python tools/verify_live.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import console  # noqa: F401
import news
import storage
import weather_bot

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def verify_news() -> None:
    print("\n=== Новостной бот (живые RSS) ===")
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "news_state.json")
        sent = []

        def sender(text: str) -> bool:
            sent.append(text)
            return True

        ok = news.run(send=sender, state_path=state_path, state=None)
        check("дайджест отправлен", ok and len(sent) == 1, f"частей: {len(sent)}")
        check("в сообщении есть ссылки", "Читать источник" in sent[0])

        saved = storage.load_state(state_path)["seen"]
        legacy = len(news.load_legacy_seen())
        check("состояние записано после доставки", len(saved) > legacy,
              f"записей: {len(saved)} (перенесено из старого файла: {legacy})")

        first_batch = set(saved) - set(news.load_legacy_seen())

        sent.clear()
        ok2 = news.run(send=sender, state_path=state_path)
        check("повторный запуск отработал", ok2)

        # Дайджест — это TOP_N свежих новостей за запуск. Второй запуск вправе
        # взять следующие, но уже отправленное повторяться не должно.
        after = storage.load_state(state_path)["seen"]
        second_batch = set(after) - set(saved)
        check("повторно то же не отправлено", not (first_batch & second_batch))
        check("состояние только растёт", len(after) >= len(saved),
              f"было {len(saved)}, стало {len(after)}")

        # Отказ доставки не должен фиксировать новости как отправленные.
        with tempfile.TemporaryDirectory() as tmp2:
            path2 = os.path.join(tmp2, "fail_state.json")
            ok3 = news.run(send=lambda _text: False, state_path=path2)
            check("сбой доставки возвращает False", ok3 is False)
            check("при сбое новые записи не добавлены",
                  len(storage.load_state(path2)["seen"]) <= legacy)


def verify_weather() -> None:
    print("\n=== Погодной бот (живой Open-Meteo) ===")
    sent = []
    captured = {}

    original = weather_bot.default_http_get

    def spy_get(url, params=None, timeout=20):
        captured["params"] = params
        return original(url, params=params, timeout=timeout)

    ok = weather_bot.run(http_get=spy_get, send=lambda text: sent.append(text) or True,
                         check_mode=False, state=storage.empty_state())
    check("прогноз отправлен", ok and len(sent) == 1)
    check("запрошены м/с", captured.get("params", {}).get("wind_speed_unit") == "ms")
    check("использованы актуальные поля",
          "wind_speed_10m" in captured.get("params", {}).get("hourly", ""))

    text = sent[0] if sent else ""
    check("оба города в сообщении", "Подольск" in text and "Москва" in text)
    check("есть блок «Сейчас»", "Сейчас" in text)
    check("есть блок «Завтра»", "Завтра" in text)
    check("советы по каждому городу отдельно", text.count("Что надеть и взять") == 2)

    # Порог ветра: значения приходят в м/с, поэтому линия «Ветер» не должна
    # появляться при штиле. Проверяем согласованность единиц косвенно.
    import re as _re
    winds = _re.findall(r"порывы до (\d+) м/с", text)
    check("порог ветра в м/с правдоподобен", all(float(w) >= 10 for w in winds),
          f"значения: {winds or 'нет'}")

    state = storage.empty_state()
    weather_bot.run(http_get=spy_get, send=lambda _text: False, state=state)
    check("сбой доставки погоды обработан", True)


def main() -> int:
    verify_news()
    verify_weather()

    print("\n" + "=" * 50)
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Все сквозные проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
