import imaplib
import email
from email.header import decode_header
import datetime
import requests
import os
import sys
import time
from email.utils import parsedate_to_datetime

# ================= НАСТРОЙКИ =================
EMAIL_USER = "danya_frolov_2006@mail.ru"
EMAIL_PASS = "Y5PJJXb3SdSTOGbiyArs"
IMAP_SERVER = "imap.mail.ru"
TG_BOT_TOKEN = "8337778471:AAEFoM9hZ7aWCxNkdJEMbA9I7CCn5j8KoiI"
TG_CHAT_ID = "962277709"

TARGET_SENDERS = [
    "online@mirea.ru",
    "oplata@mirea.ru",
    "lk@mirea.ru"
]

CHECK_INTERVAL = 8 * 60 * 60
MAX_TEXT_LENGTH = 800  # Ограничение длины текста для Телеграма (чтобы не спамил простыней)
# =============================================

def send_telegram_msg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("Ошибка: Токены Telegram не найдены.")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    params = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=params)
    except Exception as e:
        print(f"Ошибка TG: {e}")

def safe_decode(header_value):
    if not header_value: return "Неизвестно"
    decoded_list = decode_header(header_value)
    parts = []
    for content, encoding in decoded_list:
        if isinstance(content, bytes):
            parts.append(content.decode(encoding or 'utf-8', errors='ignore'))
        elif isinstance(content, str):
            parts.append(content)
    return "".join(parts)

def check_mail():
    # Проверяем письма только за последние 8 часов + небольшой запас (9 часов)
    # Чтобы точно не пропустить ничего между запусками
    time_limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=9)
    
    print(f"Ищем письма моложе {time_limit}...")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        
        # IMAP search позволяет искать по дате (только день), поэтому берем "вчера"
        # А фильтровать по часам будем уже внутри Python
        since_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        
        if status != "OK" or not messages[0]:
            print("Нет писем за последние сутки.")
            return

        for e_id in messages[0].split():
            # Скачиваем заголовки + дату
            res, msg_data = mail.fetch(e_id, "(RFC822.HEADER)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Проверка даты письма
            if msg['Date']:
                try:
                    email_date = parsedate_to_datetime(msg['Date'])
                    # Если дата без часового пояса, добавляем UTC
                    if email_date.tzinfo is None:
                         email_date = email_date.replace(tzinfo=datetime.timezone.utc)
                    
                    # Если письмо старое - пропускаем
                    if email_date < time_limit:
                        continue
                except:
                    pass # Если не смогли распарсить дату, проверяем на всякий случай

            # Декодируем
            from_raw = msg.get("From")
            sender = safe_decode(from_raw)
            subject = safe_decode(msg.get("Subject"))

            # Проверка отправителя
            is_target = False
            for target in TARGET_SENDERS:
                if target.lower() in str(from_raw).lower() or target.lower() in sender.lower():
                    is_target = True
                    break
            
            if is_target:
                print(f"Найдено: {sender}")
                clean_sender = sender.replace("<", "&lt;").replace(">", "&gt;")
                clean_subj = subject.replace("<", "&lt;").replace(">", "&gt;")
                
                tg_msg = (
                    f"🔔 <b>Свежее письмо</b> (за 8ч)\n"
                    f"👤 <b>От:</b> {clean_sender}\n"
                    f"✉️ <b>Тема:</b> {clean_subj}"
                )
                send_telegram_msg(tg_msg)

        mail.logout()
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    if not EMAIL_USER or not EMAIL_PASS:
        print("Ошибка: Логин/Пароль не заданы в переменных окружения.")
        sys.exit(1)
    check_mail()