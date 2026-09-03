import os
import sys
import re
import json
import html
import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
import feedparser
from bs4 import BeautifulSoup

# Корректный вывод UTF-8 в консоль Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ================== НАСТРОЙКИ ==================

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or os.environ.get("NEWS_TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("NEWS_TG_CHAT_ID")

SEEN_PATH = "seen.json"
TOP_N = 8  # Количество новостей в одном дайджесте
MAX_DESC_LEN = 220  # Максимальная длина описания

# Проверенные и стабильные русскоязычные RSS-ленты
FEEDS = [
    {"source": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"},
    {"source": "Хабр", "url": "https://habr.com/ru/rss/news/?fl=ru"},
    {"source": "Коммерсантъ", "url": "https://www.kommersant.ru/RSS/news.xml"},
    {"source": "3DNews", "url": "https://3dnews.ru/news/rss/"},
]

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3800


# ================== УТИЛИТЫ ==================

def load_seen() -> list:
    """Загрузка хэшей уже отправленных новостей."""
    if not os.path.exists(SEEN_PATH):
        return []
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_seen(seen_list: list):
    """Сохранение истории с ограничением до последних 500 записей."""
    seen_list = seen_list[-500:]
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_list, f, ensure_ascii=False, indent=2)

def canonicalize_url(url: str) -> str:
    """Очистка URL от отслеживающих параметров (UTM, gclid и т.д.)."""
    try:
        p = urlparse(url)
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            kl = k.lower()
            if kl.startswith("utm_") or kl in {"yclid", "gclid", "fbclid"}:
                continue
            q.append((k, v))
        new_query = urlencode(q, doseq=True)
        return urlunparse(p._replace(query=new_query, fragment=""))
    except Exception:
        return url

def clean_snippet(raw_text: str) -> str:
    """Очистка описания новости от HTML-разметки и форматирование длины."""
    if not raw_text:
        return ""
    
    # Удаляем HTML-теги с помощью BeautifulSoup
    soup = BeautifulSoup(raw_text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = html.unescape(text)
    
    # Убираем множественные пробелы и переносы
    text = re.sub(r"\s+", " ", text).strip()
    
    # Ограничиваем длину до разумного размера для Telegram
    if len(text) > MAX_DESC_LEN:
        trimmed = text[:MAX_DESC_LEN]
        last_space = trimmed.rfind(" ")
        if last_space > MAX_DESC_LEN // 2:
            trimmed = trimmed[:last_space]
        text = trimmed.rstrip(".,;:- ") + "..."
        
    return text

def send_telegram_html(text: str):
    """Отправка сообщения в Telegram с разбивкой по лимиту 4096 символов."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ TG_BOT_TOKEN или TG_CHAT_ID не заданы. Вывод в консоль:\n")
        print(text)
        return

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
    for part in parts:
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(url, json=payload, timeout=15)
            res = r.json()
            if res.get("ok"):
                print("✅ Часть дайджеста успешно отправлена")
            else:
                print(f"❌ Ошибка Telegram API: {res.get('description')}")
        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram: {e}")


# ================== СБОР НОВОСТЕЙ ==================

def collect_news() -> list:
    """Сбор и очистка новостей из всех настроенных RSS-лент."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    items = []

    for feed_info in FEEDS:
        name = feed_info["source"]
        feed_url = feed_info["url"]
        print(f"📡 Загрузка RSS [{name}]: {feed_url}")
        try:
            resp = requests.get(feed_url, headers=headers, timeout=12)
            if resp.status_code != 200:
                print(f"⚠️ Статус {resp.status_code} для {name}")
                continue
            d = feedparser.parse(resp.content)
        except Exception as e:
            print(f"❌ Ошибка загрузки RSS [{name}]: {e}")
            continue

        for entry in d.entries:
            title = entry.get("title", "").strip()
            link = canonicalize_url(entry.get("link", "").strip())
            if not title or not link:
                continue

            raw_desc = entry.get("summary", "") or entry.get("description", "")
            snippet = clean_snippet(raw_desc)
            
            # Если описание в точности дублирует заголовок, не дублируем
            if snippet.lower() == title.lower():
                snippet = ""

            h = hashlib.md5((title + link).encode("utf-8")).hexdigest()
            items.append({
                "source": name,
                "title": title,
                "link": link,
                "snippet": snippet,
                "hash": h
            })

    # Чередуем новости из разных источников для разнообразия дайджеста
    by_source = {}
    for feed_info in FEEDS:
        name = feed_info["source"]
        by_source[name] = [it for it in items if it["source"] == name]

    interleaved = []
    max_count = max((len(lst) for lst in by_source.values()), default=0)
    for idx in range(max_count):
        for name in by_source:
            if idx < len(by_source[name]):
                interleaved.append(by_source[name][idx])

    # Убираем дубликаты среди только что собранных
    unique_items = {}
    for it in interleaved:
        if it["hash"] not in unique_items:
            unique_items[it["hash"]] = it

    return list(unique_items.values())


# ================== MAIN ==================

def main():
    print("🚀 Запуск News Bot...")
    seen_hashes = set(load_seen())
    print(f"📚 Ранее отправлено новостей: {len(seen_hashes)}")

    all_news = collect_news()
    print(f"📥 Всего собрано новостей из RSS: {len(all_news)}")

    fresh = [n for n in all_news if n["hash"] not in seen_hashes]
    print(f"✨ Свежих новостей: {len(fresh)}")

    if not fresh:
        print("ℹ️ Нет новых новостей для отправки.")
        return

    # Берём TOP_N самых свежих
    selected_news = fresh[:TOP_N]

    blocks = ["📰 <b>Главные новости на 12:00 МСК</b>\n"]
    new_seen = list(seen_hashes)

    for i, item in enumerate(selected_news, 1):
        safe_source = html.escape(item["source"])
        safe_title = html.escape(item["title"])
        safe_desc = html.escape(item["snippet"])
        safe_link = html.escape(item["link"], quote=True)

        block = f"<b>{i}. [{safe_source}] {safe_title}</b>"
        if safe_desc:
            block += f"\n{safe_desc}"
        block += f"\n👉 <a href=\"{safe_link}\">Читать источник</a>"
        
        blocks.append(block)
        new_seen.append(item["hash"])

    full_message = "\n\n".join(blocks)
    send_telegram_html(full_message)
    
    save_seen(new_seen)
    print("💾 seen.json успешно обновлён.")

if __name__ == "__main__":
    main()

