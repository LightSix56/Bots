import imaplib
import email
from email.header import decode_header
import datetime
import requests
import os
import sys
from email.utils import parsedate_to_datetime

# ================= НАСТРОЙКИ =================
# 1. Секретные данные из GitHub
EMAIL_USER = os.environ.get("MAIL_USER")
EMAIL_PASS = os.environ.get("MAIL_PASS")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")  # Ваш ID берем из секретов

# 2. Токен бота (вручную)
TG_BOT_TOKEN = "ВСТАВИТЬ_ВАШ_ТОКЕН_СЮДА"  # Например "123456:ABC-Def..."

IMAP_SERVER = "imap.mail.ru"

TARGET_SENDERS = [
    "sender1@example.com",
    "boss@work.com",
    "moodle",
    "university"
]
# ==============================================

def send_telegram_msg(text):
    if not TG_CHAT_ID:
        print("Ошибка: TG_CHAT_ID не найден в секретах")
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
    # Ищем письма за последние 9 часов
    time_limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=9)
    print(f"Проверка почты (моложе {time_limit})...")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        
        since_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        
        if status != "OK" or not messages[0]:
            print("Новых писем нет.")
            return

        for e_id in messages[0].split():
            res, msg_data = mail.fetch(e_id, "(RFC822.HEADER)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            if msg['Date']:
                try:
                    email_date = parsedate_to_datetime(msg['Date'])
                    if email_date.tzinfo is None:
                         email_date = email_date.replace(tzinfo=datetime.timezone.utc)
                    if email_date < time_limit:
                        continue
                except: pass

            from_raw = msg.get("From")
            sender = safe_decode(from_raw)
            subject = safe_decode(msg.get("Subject"))

            is_target = False
            for target in TARGET_SENDERS:
                if target.lower() in str(from_raw).lower() or target.lower() in sender.lower():
                    is_target = True
                    break
            
            if is_target:
                clean_sender = sender.replace("<", "&lt;").replace(">", "&gt;")
                clean_subj = subject.replace("<", "&lt;").replace(">", "&gt;")
                tg_msg = f"🔔 <b>Новое письмо</b>\n👤 {clean_sender}\n✉️ {clean_subj}"
                send_telegram_msg(tg_msg)

        mail.logout()
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    if not EMAIL_USER or not EMAIL_PASS:
        print("❌ Ошибка: Нет паролей от почты в секретах!")
        sys.exit(1)
    check_mail()
