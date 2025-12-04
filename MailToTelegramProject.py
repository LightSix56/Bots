import imaplib
import email
from email.header import decode_header
import telebot
from bs4 import BeautifulSoup
import os

# ================= НАСТРОЙКИ =================

EMAIL_USER = os.environ.get("MAIL_USER")
EMAIL_PASS = os.environ.get("MAIL_PASS")
CHAT_ID = os.environ.get("TG_CHAT_ID")
BOT_TOKEN = "8337778471:AAEFoM9hZ7aWCxNkdJEMbA9I7CCn5j8KoiI"  # Или os.environ.get("TG_BOT_TOKEN")

IMAP_SERVER = "imap.mail.ru"  # Для Mail.ru

# --- СПИСОК ВАЖНЫХ АДРЕСОВ ---
# Бот будет присылать письма ТОЛЬКО если они пришли от этих email-ов.
# Можно писать часть адреса (например, "@mirea.ru" захватит всех с домена mirea)
ALLOWED_SENDERS = [
    "online@mirea.ru",     # Уведомления о заданиях
    "ump@mirea.ru", # Другие уведомления вуза
    "otsrochka@mirea.ru",      # Конкретный препод
    "@mirea.ru"                # Любой адрес с концовкой @mirea.ru
]

# ================= ЛОГИКА =================

def clean_text(text):
    if not text: return ""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(filter(None, lines))

def get_email_body_with_links(msg):
    """Парсит текст и достает ссылки"""
    body = ""
    html_content = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if "attachment" in content_disposition: continue

            try:
                payload = part.get_payload(decode=True)
                if not payload: continue
                charset = part.get_content_charset() or 'utf-8'
                decoded_part = payload.decode(charset, errors="ignore")
                
                if content_type == "text/html":
                    html_content = decoded_part
                elif content_type == "text/plain" and html_content is None:
                    body = decoded_part
            except: pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or 'utf-8'
            decoded_part = payload.decode(charset, errors="ignore")
            if msg.get_content_type() == "text/html":
                html_content = decoded_part
            else:
                body = decoded_part
        except: pass

    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        for a in soup.find_all('a', href=True):
            url = a['href']
            text = a.get_text(strip=True)
            if url and text:
                a.replace_with(f" {text} [ {url} ] ")
            elif url:
                a.replace_with(f" [ {url} ] ")
        body = soup.get_text(separator="\n")

    return clean_text(body)

def is_sender_allowed(sender_str):
    """Проверяет, есть ли отправитель в белом списке"""
    sender_str = sender_str.lower() # Приводим к нижнему регистру для надежности
    for allowed in ALLOWED_SENDERS:
        if allowed.lower() in sender_str:
            return True
    return False

def check_email():
    if not EMAIL_USER or not EMAIL_PASS:
        print("Ошибка: Нет секретов!")
        return

    bot = telebot.TeleBot(BOT_TOKEN)

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # Ищем ВСЕ непрочитанные письма
        status, messages = mail.search(None, 'UNSEEN')
        email_ids = messages[0].split()

        if not email_ids:
            print("Новых писем нет.")
            return

        print(f"Найдено {len(email_ids)} непрочитанных писем. Фильтруем...")

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Декодируем отправителя
                    sender, encoding = decode_header(msg["From"])[0]
                    if isinstance(sender, bytes):
                        sender = sender.decode(encoding if encoding else "utf-8", errors="ignore")

                    # === ГЛАВНЫЙ ФИЛЬТР ===
                    if not is_sender_allowed(sender):
                        print(f"Пропущено письмо от: {sender} (нет в белом списке)")
                        continue # Пропускаем это письмо, идем к следующему
                    
                    # Если отправитель "Наш" - обрабатываем дальше
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")

                    body = get_email_body_with_links(msg)

                    if len(body) > 3500:
                        body = body[:3500] + "\n..."

                    text_message = (
                        f"📩 <b>Важное письмо!</b>\n\n"
                        f"👤 <b>От:</b> {sender}\n"
                        f"📝 <b>Тема:</b> {subject}\n\n"
                        f"{body}"
                    )

                    try:
                        bot.send_message(CHAT_ID, text_message, parse_mode="HTML", disable_web_page_preview=True)
                        print(f"Отправлено письмо от {sender}")
                    except Exception as e:
                        print(f"Ошибка отправки: {e}")
                        bot.send_message(CHAT_ID, text_message.replace("<", "").replace(">", ""))

        mail.close()
        mail.logout()

    except Exception as e:
        print(f"Ошибка IMAP: {e}")

if __name__ == "__main__":
    check_email()
