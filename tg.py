"""Общий транспорт Telegram для всех ботов проекта.

Инкапсулирует три вещи, которые раньше были скопированы в каждый скрипт
с расхождениями:

1. Нарезку длинных сообщений по лимиту Telegram (4096 символов) так, чтобы
   не разорвать HTML-сущность или тег.
2. Повторные попытки при 429 (с учётом retry_after) и 5xx.
3. Откат на отправку без parse_mode, если разметка не разобралась (400).

Повторы делаются только там, где достоверно известно, что сообщение не принято:
400 (разметка), 429 (лимит), 5xx (сбой сервера). Если ответ не получен вовсе,
повтор не выполняется: неизвестно, дошло ли сообщение, а ключей идемпотентности
у Telegram нет — повтор создал бы дубль.

send_message возвращает bool. Вызывающий код обязан использовать результат:
помечать письмо прочитанным или сохранять историю можно только при True,
иначе при сбое доставки данные теряются безвозвратно.
"""

from __future__ import annotations

import html
import re
import time
from typing import Callable, List, Optional

import requests

import console  # noqa: F401  (импорт настраивает UTF-8 вывод в консоль)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096
SAFE_MESSAGE_LEN = 3800

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_TAIL_RE = re.compile(r"&[A-Za-z#0-9]{0,8}$")

Poster = Callable[[str, dict, float], dict]


def strip_html(text: str) -> str:
    """Превращает наш HTML в обычный текст для отправки без parse_mode."""
    return html.unescape(_TAG_RE.sub("", text))


def _hard_split(line: str, limit: int) -> List[str]:
    """Режет строку без переносов, не разрывая HTML-сущность (&amp;, &#33;)."""
    pieces: List[str] = []
    rest = line
    while len(rest) > limit:
        cut = limit
        amp = rest.rfind("&", max(0, cut - 10), cut)
        if amp != -1 and ";" not in rest[amp:cut]:
            cut = amp
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


def split_message(text: str, limit: int = SAFE_MESSAGE_LEN) -> List[str]:
    """Делит сообщение на части не длиннее limit, разрезая по границам строк.

    Гарантии:
    * ни одна часть не превышает limit;
    * ни один символ не теряется: части по порядку содержат весь текст;
    * пустые строки сохраняются. Наивная сборка буфера через "buf = line, если
      буфер пуст" теряла пустую строку, попавшую сразу после границы части, и
      абзацы в сообщении слипались;
    * если отдельная строка длиннее limit, её приходится резать жёстко — тогда
      при обратной сборке в этом месте появится перевод строки, которого в
      исходном тексте не было. Границы HTML-сущностей (&amp;, &#33;) при жёстком
      разрезе не разрываются.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

    for line in text.split("\n"):
        if len(line) > limit:
            # Строку нельзя уместить в часть целиком: режем жёстко.
            flush()
            pieces = _hard_split(line, limit)
            chunks.extend(pieces[:-1])
            current = [pieces[-1]]
            current_len = len(pieces[-1])
            continue

        added = len(line) + (1 if current else 0)
        if current_len + added > limit:
            flush()
            added = len(line)
        current.append(line)
        current_len += added

    flush()
    return chunks


def _default_poster(url: str, payload: dict, timeout: float) -> dict:
    """HTTP POST, возвращающий разобранный JSON ответа Telegram."""
    resp = requests.post(url, json=payload, timeout=timeout)
    try:
        return resp.json()
    except ValueError:
        return {
            "ok": False,
            "error_code": resp.status_code,
            "description": f"HTTP {resp.status_code}: ответ не является JSON",
        }


def _post_chunk(url: str, payload: dict, timeout: float, poster: Poster) -> Optional[dict]:
    try:
        return poster(url, payload, timeout)
    except Exception as exc:  # сетевые сбои, таймауты, DNS
        print(f"❌ Ошибка запроса к Telegram: {exc}")
        return None


def _send_chunk(
    poster: Poster,
    url: str,
    chat_id: str,
    chunk: str,
    parse_mode: Optional[str],
    disable_preview: bool,
    timeout: float,
    max_attempts: int,
    sleep: Callable[[float], None],
) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": chunk,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        res = _post_chunk(url, payload, timeout, poster)
        if isinstance(res, dict) and res.get("ok"):
            return True

        code = (res or {}).get("error_code")
        desc = (res or {}).get("description", "нет ответа")

        # Разметка не разобралась: Telegram отдаёт 400 и текст не уходит.
        # Отправляем ту же часть как обычный текст, чтобы не потерять содержимое.
        if code == 400 and parse_mode:
            print(f"🔁 Telegram отверг HTML ({desc}), повтор без разметки")
            plain_payload = {
                "chat_id": chat_id,
                "text": strip_html(chunk),
                "disable_web_page_preview": disable_preview,
            }
            res_plain = _post_chunk(url, plain_payload, timeout, poster)
            if isinstance(res_plain, dict) and res_plain.get("ok"):
                return True
            print(f"❌ Часть сообщения не доставлена: {(res_plain or {}).get('description')}")
            return False

        if code == 429 and attempt < max_attempts:
            params = (res or {}).get("parameters") or {}
            try:
                wait = float(params.get("retry_after", delay))
            except (TypeError, ValueError):
                wait = delay
            print(f"⏳ Лимит Telegram, ждём {wait:.0f} с")
            sleep(wait)
            continue

        # 5xx означает, что сообщение не принято, — повтор безопасен.
        if code is not None and 500 <= int(code) < 600 and attempt < max_attempts:
            print(f"⚠️ Telegram вернул {code}, повтор через {delay:.0f} с")
            sleep(delay)
            delay *= 2
            continue

        if res is None:
            # Ответа нет: неизвестно, дошло ли сообщение. Telegram не поддерживает
            # ключи идемпотентности, поэтому немедленный повтор рискует отправить
            # дубль. Возвращаем False: вызывающий код не сохранит состояние, и
            # попытка повторится в следующий плановый запуск.
            print("❓ Ответ Telegram не получен — доставка не подтверждена, повтор не делаем")
            return False

        print(f"❌ Ошибка Telegram API: {desc}")
        return False

    return False


def send_message(
    text: str,
    token: Optional[str],
    chat_id: Optional[str],
    *,
    parse_mode: Optional[str] = "HTML",
    disable_preview: bool = True,
    timeout: float = 15,
    max_attempts: int = 3,
    poster: Optional[Poster] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Отправляет текст в Telegram, разбивая его по лимиту длины.

    Возвращает True только если доставлены ВСЕ части. При частичной доставке
    вызывающий код не должен сохранять состояние: повтор лучше пропуска.
    """
    if not token or not chat_id:
        raise ValueError("TG_BOT_TOKEN и TG_CHAT_ID обязательны")
    if not text or not text.strip():
        raise ValueError("текст сообщения пуст")

    post = poster or _default_poster
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    chunks = split_message(text)

    delivered = 0
    for chunk in chunks:
        if _send_chunk(
            post, url, chat_id, chunk, parse_mode, disable_preview, timeout, max_attempts, sleep
        ):
            delivered += 1

    if delivered == len(chunks):
        print(f"✅ Отправлено в Telegram (частей: {delivered})")
        return True

    print(f"⚠️ Доставлено {delivered} из {len(chunks)} частей — состояние не сохраняем")
    return False
