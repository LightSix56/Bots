import os
import re
import json
import html
import hashlib

import requests
import feedparser
from gigachat import GigaChat


# ================== КОНФИГ ==================

# Секреты из GitHub
GIGA_KEY = os.environ.get("GIGA_CREDENTIALS")
TG_BOT_TOKEN = '8549981113:AAHM8q2C2e8VvFAjFSfgcR2HZtQUw6LVFqU' # Лучше брать из секретов, но можно вписать и вручную
TG_CHAT_ID = -5067157804
# Файл с уже отправленными новостями
SEEN_PATH = "seen.json"

# Сколько новостей за утро
TOP_N = 10

# RSS‑источники (можно расширять)
FEEDS = [
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru&topic=WORLD",
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru&topic=BUSINESS",
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru&topic=TECHNOLOGY",
]

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3900  # оставляем запас под HTML‑сущности


# ================== УТИЛИТЫ ==================

def load_seen():
    if not os.path.exists(SEEN_PATH):
        return []
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_seen(seen_list):
    # ограничиваем длину истории, чтобы файл не раздувался
    seen_list = seen_list[-500:]
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_list, f, ensure_ascii=False, indent=2)


def canonicalize_url(url: str) -> str:
    """Убираем UTM и трекинг-параметры, чтобы одни и те же новости не дублировались."""
    try:
        from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
        p = urlparse(url)
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            kl = k.lower()
            if kl.startswith("utm_"):
                continue
            if kl in {"yclid", "gclid", "fbclid"}:
                continue
            q.append((k, v))
        new_query = urlencode(q, doseq=True)
        return urlunparse(p._replace(query=new_query, fragment=""))
    except Exception:
        return url


def cleanup_llm_text(text: str) -> str:
    """Удаляем Markdown‑символы, чтобы не ломать HTML/визуал в ТГ."""
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"``````", "", t, flags=re.S)  # code fences
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = re.sub(r"(?m)^\s*[-•]+\s*", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def send_telegram_html(text: str):
    """Отправка текста в Telegram c учётом HTML и лимита длины."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ Нет TG_BOT_TOKEN или TG_CHAT_ID")
        return

    text = text or ""
    if not text.strip():
        print("⚠️ Пустое сообщение, не отправляю")
        return

    # Аккуратно режем по строкам, чтобы не рвать теги
    parts = []
    buf = ""
    for line in text.split("\n"):
        add = line + "\n"
        if len(buf) + len(add) > SAFE_LIMIT:
            parts.append(buf.rstrip("\n"))
            buf = add
        else:
            buf += add
    if buf.strip():
        parts.append(buf.rstrip("\n"))

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

    for idx, chunk in enumerate(parts, 1):
        if len(chunk) > TELEGRAM_LIMIT:
            chunk = chunk[:SAFE_LIMIT]

        payload = {
            "chat_id": TG_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        print(f"[TG] sending part {idx}/{len(parts)}, len={len(chunk)}")
        r = requests.post(url, json=payload)
        print("TG status:", r.status_code, r.text)


# ================== GIGACHAT ==================

def get_news_summary(title: str, snippet: str) -> str:
    """Краткое саммари новости через GigaChat (как в погодном боте)."""
    prompt = f"""
Ты русский новостной редактор.
Прочитай заголовок и отрывок новости и сделай краткое саммари на русском языке.
Требования:
- 1–2 предложения.
- Без Markdown, без звёздочек, без эмодзи.
- Никаких ссылок и хэштегов.

Заголовок: {title}
Отрывок: {snippet}
"""
    try:
        with GigaChat(credentials=GIGA_KEY, verify_ssl_certs=False) as giga:
            resp = giga.chat(prompt)
            text = resp.choices[0].message.content
            return cleanup_llm_text(text)
    except Exception as e:
        print(f"Ошибка GigaChat: {e}")
        return "Не удалось получить краткое содержание."


# ================== НОВОСТИ ==================

def collect_news():
    """Собираем новости из RSS, фильтруем и возвращаем список словарей."""
    items = []

    for feed_url in FEEDS:
        print("Парсим RSS:", feed_url)
        d = feedparser.parse(feed_url)
        for entry in d.entries:
            title = entry.get("title", "").strip()
            link = canonicalize_url(entry.get("link", "").strip())
            if not title or not link:
                continue

            snippet = entry.get("summary", "") or entry.get("description", "")
            snippet = re.sub(r"<.*?>", " ", snippet or "")
            snippet = re.sub(r"\s+", " ", snippet).strip()

            h = hashlib.md5((title + link).encode("utf-8")).hexdigest()

            items.append({
                "title": title,
                "link": link,
                "snippet": snippet,
                "hash": h,
            })

    # Убираем возможные дубликаты по hash
    uniq = {}
    for it in items:
        if it["hash"] not in uniq:
            uniq[it["hash"]] = it
    return list(uniq.values())


# ================== MAIN ==================

def main():
    if not GIGA_KEY:
        print("❌ Нет GIGA_CREDENTIALS в переменных окружения!")
        return

    seen_hashes = load_seen()
    print("Загружено уже отправленных новостей:", len(seen_hashes))

    all_news = collect_news()
    print("Всего найдено новостей:", len(all_news))

    # Фильтруем уже отправленные
    fresh = [n for n in all_news if n["hash"] not in seen_hashes]
    print("Новых (не отправляли раньше):", len(fresh))

    if not fresh:
        send_telegram_html("☕ <b>Главные новости на утро</b>\n\nСегодня нет новых новостей без повторов.")
        return

    # Берём TOP_N самых верхних (RSS обычно уже отсортирован по дате)
    final_news = fresh[:TOP_N]

    blocks = []
    header = "☕ <b>Главные новости на утро</b>\n"
    blocks.append(header)

    new_seen = seen_hashes.copy()

    for i, news in enumerate(final_news, 1):
        summary = get_news_summary(news["title"], news["snippet"])

        safe_title = html.escape(news["title"])
        safe_summary = html.escape(summary)
        safe_link = html.escape(news["link"], quote=True)

        block = (
            f"<b>{i}. {safe_title}</b>\n"
            f"{safe_summary}\n"
            f"<a href=\"{safe_link}\">Читать источник</a>"
        )
        blocks.append(block)

        new_seen.append(news["hash"])

    full_text = "\n\n".join(blocks)

    print("Длина итогового текста:", len(full_text))
    print("Первые 500 символов:\n", full_text[:500])

    send_telegram_html(full_text)
    save_seen(new_seen)
    print("Готово, seen.json обновлён.")


if __name__ == "__main__":
    main()
