import os
import json
import html
import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests
from gigachat import GigaChat  # Используем нативную библиотеку

# ========== НАСТРОЙКИ ==========
# 1. Секреты (как в твоем примере)
GIGA_KEY = os.environ.get("GIGA_CREDENTIALS")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") # Лучше брать из секретов, но можно вписать и вручную
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# Файл для хранения истории (чтобы не было повторов)
SEEN_PATH = "seen.json"
TOP_N = 10  # Количество новостей

# Источники (Google News Russia)
FEEDS = [
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru&topic=WORLD",
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru&topic=BUSINESS",
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru&topic=TECHNOLOGY",
]
# ================================

# --- Служебные функции (чистка текста, ссылок) ---

def cleanup_text(text):
    """Убирает Markdown (**bold**, __italic__), который может сломать HTML в ТГ"""
    if not text: return ""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text) # убираем жирный **text**
    text = re.sub(r"__(.*?)__", r"\1", text)     # убираем курсив __text__
    text = text.replace("`", "")                 # убираем код
    return text.strip()

def canonicalize_url(url):
    """Убирает UTM-метки, чтобы не дублировать одну и ту же новость с разными хвостами"""
    try:
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in ("yclid", "gclid")]
        return urlunparse(p._replace(query=urlencode(q, doseq=True), fragment=""))
    except:
        return url

def get_news_summary(title, snippet):
    """Суммаризация через библиотеку GigaChat (как в примере с погодой)"""
    prompt = f"""
Прочитай заголовок и отрывок новости.
Заголовок: {title}
Отрывок: {snippet}

Напиши краткое содержание (самари) этой новости на русском языке.
Всего 1-2 предложения.
Не используй Markdown (звездочки, решетки). Пиши обычным текстом.
"""
    try:
        # verify_ssl_certs=False, как в твоем коде
        with GigaChat(credentials=GIGA_KEY, verify_ssl_certs=False) as giga:
            resp = giga.chat(prompt)
            return cleanup_text(resp.choices[0].message.content)
    except Exception as e:
        print(f"Ошибка GigaChat: {e}")
        return "Не удалось получить краткое содержание."

def load_seen():
    if not os.path.exists(SEEN_PATH): return []
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return []

def save_seen(seen_list):
    # Храним только последние 500 хешей, чтобы файл не раздувался
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_list[-500:], f)

# --- Основная логика ---

def send_telegram_html(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("Ошибка: Нет токена или ID чата")
        return
    
    # Telegram имеет лимит 4096 символов. Если текст длинный, режем.
    # Для простоты режем грубо, если вдруг выйдет за лимиты.
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TG_CHAT_ID, 
                "text": chunk, 
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })
            if not r.ok: print(f"Ошибка TG: {r.text}")
        except Exception as e:
            print(f"Ошибка отправки: {e}")

def main():
    if not GIGA_KEY:
        print("❌ Ошибка: Нет ключа GigaChat (GIGA_CREDENTIALS)!")
        return

    seen_hashes = load_seen()
    new_seen_hashes = seen_hashes.copy()
    
    collected_news = []
    
    # 1. Сбор новостей
    for feed in FEEDS:
        d = feedparser.parse(feed)
        for entry in d.entries:
            title = entry.get("title", "")
            link = canonicalize_url(entry.get("link", ""))
            
            # Создаем уникальный отпечаток новости (хэш)
            news_hash = hashlib.md5((title + link).encode()).hexdigest()
            
            if news_hash in new_seen_hashes:
                continue # Уже видели
            
            # Берем snippet для контекста
            snippet = entry.get("summary", "") or entry.get("description", "")
            snippet = re.sub(r"<.*?>", "", snippet) # убрать html теги из rss
            
            collected_news.append({
                "title": title,
                "link": link,
                "snippet": snippet,
                "hash": news_hash
            })

    # 2. Отбор топ-N (свежих)
    # RSS обычно уже отсортирован, берем первые N уникальных
    final_news = collected_news[:TOP_N]
    
    if not final_news:
        print("Нет новых новостей.")
        return

    # 3. Обработка через AI и формирование сообщения
    message_blocks = []
    message_blocks.append(f"☕ <b>Главные новости на утро</b>\n")
    
    print(f"Обработка {len(final_news)} новостей...")
    
    for i, news in enumerate(final_news, 1):
        summary = get_news_summary(news["title"], news["snippet"])
        
        # Формируем HTML блок
        # escape нужен, чтобы спецсимволы < > не сломали разметку ТГ
        safe_title = html.escape(news["title"])
        safe_summary = html.escape(summary)
        
        block = (
            f"<b>{i}. {safe_title}</b>\n"
            f"{safe_summary}\n"
            f"<a href='{news['link']}'>Читать источник</a>"
        )
        message_blocks.append(block)
        
        # Добавляем в просмотренные
        new_seen_hashes.append(news["hash"])

    # 4. Отправка
    full_text = "\n\n".join(message_blocks)
    send_telegram_html(full_text)
    
    # 5. Сохранение истории
    save_seen(new_seen_hashes)

if __name__ == "__main__":
    main()
