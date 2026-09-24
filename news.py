"""Новостной дайджест из RSS-лент.

Что изменилось по сравнению с прежней версией:

* Каждая лента грузится с явным таймаутом; отказ одной ленты не отменяет
  остальные, а попадает в сводку как «источник недоступен».
* Учитывается дата публикации (published_parsed): дайджест сортируется по
  свежести, древние записи (старше MAX_AGE_DAYS) отбрасываются, чтобы в ленту
  не попадали материалы недельной давности.
* История отправленного пишется только при подтверждённой доставке. Раньше
  хэши сохранялись даже при сбое Telegram, и новость исчезала навсегда.
* Заголовок сообщения содержит фактическое время запуска, а не «12:00 МСК».
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup

import config
import storage
import tg

MSK = timezone(timedelta(hours=3))
STATE_PATH = os.environ.get("STATE_PATH", "data/state.json")
LEGACY_SEEN_PATH = "seen.json"

TOKEN_ENVS = ("NEWS_TG_BOT_TOKEN",)

HTTP_TIMEOUT = 15
MAX_DESC_LEN = 220
MAX_AGE_DAYS = 3
SEEN_TTL_DAYS = 90
ALERT_COOLDOWN_HOURS = 6
TOP_N = 8

TRACKING_PARAMS = {"yclid", "gclid", "fbclid", "igshid", "mc_eid"}

FEEDS: Sequence[dict] = (
    {"source": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"},
    {"source": "Хабр", "url": "https://habr.com/ru/rss/news/?fl=ru"},
    {"source": "Коммерсантъ", "url": "https://www.kommersant.ru/RSS/news.xml"},
    {"source": "3DNews", "url": "https://3dnews.ru/news/rss/"},
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def canonicalize_url(url: str) -> str:
    """Убирает отслеживающие параметры, чтобы дедупликация не ломалась на UTM."""
    try:
        parsed = urlparse(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
        ]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))
    except (ValueError, AttributeError):
        return url


def clean_snippet(raw_text: Optional[str]) -> str:
    """Очищает описание от HTML и обрезает по границе слова."""
    if not raw_text:
        return ""
    text = BeautifulSoup(raw_text, "html.parser").get_text(separator=" ", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_DESC_LEN:
        trimmed = text[:MAX_DESC_LEN]
        last_space = trimmed.rfind(" ")
        if last_space > MAX_DESC_LEN // 2:
            trimmed = trimmed[:last_space]
        text = trimmed.rstrip(".,;:- ") + "..."
    return text


def _entry_datetime(entry: Mapping) -> Optional[datetime]:
    """Дата публикации записи в UTC, если лента её отдала."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def parse_feed(raw: bytes, source: str, *, now: Optional[datetime] = None) -> List[dict]:
    """Разбирает содержимое ленты в список новостей.

    Битый XML feedparser обычно не выбрасывает исключение, а возвращает пустой
    результат; на всякий случай возможные сбои тоже гасятся.
    """
    _ = now
    try:
        parsed = feedparser.parse(raw)
    except Exception as exc:  # pragma: no cover - защита от неожиданных сбоев парсера
        print(f"❌ Не удалось разобрать ленту [{source}]: {exc}")
        return []

    items: List[dict] = []
    for entry in getattr(parsed, "entries", []) or []:
        title = (entry.get("title") or "").strip()
        link = canonicalize_url((entry.get("link") or "").strip())
        if not title or not link:
            continue
        raw_desc = entry.get("summary") or entry.get("description") or ""
        snippet = clean_snippet(raw_desc)
        if snippet.lower() == title.lower():
            snippet = ""
        digest = hashlib.md5((title + link).encode("utf-8")).hexdigest()
        items.append({
            "source": source,
            "title": title,
            "link": link,
            "snippet": snippet,
            "hash": digest,
            "published": _entry_datetime(entry),
        })
    return items


def filter_fresh(items: Iterable[dict], seen_keys: Iterable[str]) -> List[dict]:
    seen = set(seen_keys)
    return [item for item in items if item["hash"] not in seen]


def sort_by_recency(items: Iterable[dict]) -> List[dict]:
    """Свежие сверху. Записи без даты уходят в конец, сохраняя порядок ленты."""
    dated = [item for item in items if item.get("published") is not None]
    undated = [item for item in items if item.get("published") is None]
    dated.sort(key=lambda item: item["published"], reverse=True)
    return dated + undated


def drop_stale(items: Iterable[dict], *, now: datetime, max_age_days: int) -> List[dict]:
    horizon = now.astimezone(timezone.utc) - timedelta(days=max_age_days)
    kept = []
    for item in items:
        published = item.get("published")
        if published is None or published >= horizon:
            kept.append(item)
    return kept


def select_digest(items: Sequence[dict], *, limit: int) -> List[dict]:
    """Чередует источники, сохраняя внутри каждого порядок по свежести.

    Порядок источников — по времени самой свежей их новости. Так дайджест не
    превращается в «два верхних пункта РБК и всё», когда у другой ленты есть
    материал свежее.
    """
    if limit <= 0:
        return []
    groups: Dict[str, List[dict]] = {}
    order: List[str] = []
    for item in items:
        source = item["source"]
        if source not in groups:
            groups[source] = []
            order.append(source)
        groups[source].append(item)

    selected: List[dict] = []
    index = 0
    while len(selected) < limit:
        added = False
        for source in order:
            bucket = groups[source]
            if index < len(bucket):
                selected.append(bucket[index])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        index += 1
    return selected


def format_digest(
    items: Sequence[dict],
    now: datetime,
    failures: Optional[Sequence[str]] = None,
) -> str:
    if not items:
        return f"📰 <b>Новости на {now.strftime('%H:%M')} МСК</b>\n\nНет новых новостей."

    blocks = [f"📰 <b>Новости на {now.strftime('%H:%M')} МСК</b> · {now.strftime('%d.%m.%Y')}"]
    for index, item in enumerate(items, 1):
        block = f"<b>{index}. [{html.escape(item['source'])}] {html.escape(item['title'])}</b>"
        if item.get("snippet"):
            block += f"\n{html.escape(item['snippet'])}"
        block += f'\n👉 <a href="{html.escape(item["link"], quote=True)}">Читать источник</a>'
        blocks.append(block)

    if failures:
        names = ", ".join(html.escape(name) for name in failures)
        blocks.append(f"⚠️ Недоступны источники: {names}")

    return "\n\n".join(blocks)


def default_http_get(url: str, headers: Optional[dict] = None, timeout: float = HTTP_TIMEOUT) -> bytes:
    response = requests.get(
        url,
        headers=headers or {"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


def _default_send(text: str) -> bool:
    token, chat_id = config.telegram_credentials(TOKEN_ENVS)
    return tg.send_message(text, token, chat_id, disable_preview=True)


def build_alert_message(failures: Sequence[str]) -> str:
    return "⚠️ Не удалось получить ни одной ленты: " + ", ".join(failures)


def load_legacy_seen(path: Optional[str] = None) -> Dict[str, str]:
    """Читает историю старого формата (seen.json в корне репозитория).

    Нужна один раз при переезде состояния в кэш Actions: без неё первые запуски
    заново разослали бы новости, отправленные ранее.
    """
    target = LEGACY_SEEN_PATH if path is None else path
    if not os.path.exists(target):
        return {}
    seen = storage.load_state(target)["seen"]
    if seen:
        print(f"♻️ Перенос истории из {target}: {len(seen)} записей")
    return seen


def run(
    *,
    feeds: Optional[Sequence[dict]] = None,
    http_get: Optional[Callable] = None,
    send: Optional[Callable[[str], bool]] = None,
    send_alert: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
    check_mode: bool = False,
    state: Optional[dict] = None,
    state_path: str = STATE_PATH,
    limit: int = TOP_N,
) -> bool:
    """Собирает дайджест и отправляет его. True — доставлено (или нечего слать)."""
    moment = (now or datetime.now(MSK)).astimezone(MSK)
    getter = http_get or default_http_get
    feed_list = list(FEEDS if feeds is None else feeds)
    if state is None:
        state = storage.load_state(state_path)
        if not state["seen"]:
            state["seen"] = load_legacy_seen()

    collected: List[dict] = []
    failures: List[str] = []

    for info in feed_list:
        name, url = info["source"], info["url"]
        print(f"📡 Загрузка RSS [{name}]")
        try:
            raw = getter(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
            items = parse_feed(raw, name, now=moment)
            if not items:
                raise ValueError("лента пуста или не разобралась")
            collected.extend(items)
            print(f"✅ {name}: {len(items)} записей")
        except Exception as exc:
            failures.append(name)
            print(f"❌ Ошибка загрузки [{name}]: {exc}")

    if not collected:
        message = build_alert_message(failures or [info["source"] for info in feed_list])
        print(message)
        if not check_mode:
            _send_alert(send_alert or send, state, state_path, "news:all-feeds-failed", message, moment)
        return False

    fresh = filter_fresh(collected, state["seen"])
    fresh = drop_stale(fresh, now=moment, max_age_days=MAX_AGE_DAYS)
    fresh = sort_by_recency(fresh)
    print(f"📥 Записей собрано: {len(collected)}, новых: {len(fresh)}")

    if not fresh:
        print("ℹ️ Новых новостей нет.")
        return True

    selected = select_digest(fresh, limit=limit)
    text = format_digest(selected, moment, failures)

    if check_mode:
        print(text)
        return True

    sender = send or _default_send
    try:
        delivered = bool(sender(text))
    except Exception as exc:
        print(f"❌ Не удалось отправить дайджест: {exc}")
        delivered = False

    if not delivered:
        print("⚠️ Дайджест не доставлен — история не обновляется, повторим в следующий запуск")
        return False

    # История обновляется только по доставленным новостям. Если Telegram не принял
    # сообщение, хэши не сохраняются и новость уйдёт в следующий дайджест.
    storage.add_seen(state["seen"], [item["hash"] for item in selected], now=moment)
    state["seen"] = storage.prune_seen(state["seen"], days=SEEN_TTL_DAYS, now=moment)
    storage.save_state_atomic(state_path, state)
    print(f"💾 История обновлена: {len(state['seen'])} записей")
    return True


def _send_alert(sender, state, state_path, name, message, moment) -> None:
    if sender is None:
        return
    if not storage.should_alert(state["alerts"], name, cooldown_hours=ALERT_COOLDOWN_HOURS, now=moment):
        print(f"ℹ️ Уведомление «{name}» подавлено (кулдаун)")
        return
    try:
        if sender(message):
            storage.record_alert(state["alerts"], name, now=moment)
            storage.save_state_atomic(state_path, state)
    except Exception as exc:
        print(f"❌ Не удалось отправить уведомление: {exc}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    check_mode = "--check" in arguments or config.flag("CHECK_MODE")
    return 0 if run(check_mode=check_mode) else 1


if __name__ == "__main__":
    raise SystemExit(main())
