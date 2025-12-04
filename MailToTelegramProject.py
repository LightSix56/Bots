import imaplib
import email
from email.header import decode_header
import telebot
from bs4 import BeautifulSoup
import os  # Для работы с переменными окружения (секретами)

# ================= НАСТРОЙКИ =================

# Получаем данные из секретов GitHub Actions
# Важно: os.environ.get("ИМЯ_СЕКРЕТА") должно совпадать с тем, что в .yml файле
EMAIL_USER = os.environ.get("MAIL_USER")
EMAIL_PASS = os.environ.get("MAIL_PASS")
CHAT_ID = os.environ.get("TG_CHAT_ID")

# Если токена бота нет в секретах, вставьте его сюда строкой.
# Если он тоже в секретах, замените на os.environ.get("TG_BOT_TOKEN")
BOT_TOKEN = "8337778471:AAEFoM9hZ7aWCxNkdJEMbA9I7CCn5j8KoiI"  

# Сервер вашей почты (если Yandex). Если Gmail/Mail.ru - поменяйте.
IMAP_SERVER = "imap.mail.ru"  

# ================= ЛОГИКА ПАРСИНГА =================

def clean_text(text):
    """Удаляет пустые строки для красоты"""
    if not text: return ""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(filter(None, lines))

def get_email_body_with_links(msg):
    """
    Главная функция: достает текст и делает ссылки видимыми.
    Превращает <a href="URL">Текст</a> -> Текст: [ URL ]
    """
    body = ""
    html_content = None

    # 1. Разбираем письмо на части (Текст и HTML)
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            # Пропускаем вложения (файлы)
            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if not payload: continue
                
                # Определяем кодировку
                charset = part.get_content_charset() or 'utf-8'
                decoded_part = payload.decode(charset, errors="ignore")
                
                if content_type == "text/html":
                    html_content = decoded_part
                elif content_type == "text/plain" and html_content is None:
                    body = decoded_part
            except Exception as e:
                print(f"Ошибка декодирования части письма: {e}")
    else:
        # Письмо из одной части
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or 'utf-8'
            decoded_part = payload.decode(charset, errors="ignore")
            
            if msg.get_content_type() == "text/html":
                html_content = decoded_part
            else:
                body = decoded_part
        except Exception as e:
            print(f"Ошибка декодирования простого письма: {e}")

    # 2. Если нашли HTML, ищем в нем ссылки
    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Находим все ссылки <a>
        for a in soup.find_all('a', href=True):
            url = a['href']
            text = a.get_text(strip=True)
            
            # Если ссылка скрыта под текстом (например "Перейти к заданию")
            if url and text:
                # Меняем на формат: Текст [ Ссылка ]
                new_tag = soup.new_string(f" {text} [ {url} ] ")
                a.replace_with(new_tag)
            elif url:
                a.replace_with(f" [ {url} ] ")
        
        # Достаем чистый текст уже с раскрытыми ссылками
        body = soup.get_text(separator="\n")

    return clean_text(body)

# ================= ОСНОВНАЯ ФУНКЦИЯ =================

def check_email():
    if not EMAIL_USER or not EMAIL_PASS:
        print("Ошибка: Не заданы логин/пароль в секретах GitHub!")
        return

    bot = telebot.TeleBot(BOT_TOKEN)

    try:
        # Подключение к почте
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # Ищем только НЕПРОЧИТАННЫЕ письма (UNSEEN)
        status, messages = mail.search(None, 'UNSEEN')
        email_ids = messages[0].split()

        if not email_ids:
            print("Новых писем нет.")
            return

        print(f"Найдено новых писем: {len(email_ids)}")

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Тема
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                    
                    # Отправитель
                    sender, encoding = decode_header(msg["From"])[0]
                    if isinstance(sender, bytes):
                        sender = sender.decode(encoding if encoding else "utf-8", errors="ignore")

                    # Текст письма (с сылками!)
                    body = get_email_body_with_links(msg)

                    # Если письмо слишком длинное для Telegram
                    if len(body) > 3500:
                        body = body[:3500] + "\n...(письмо обрезано)"

                    text_message = (
                        f"📩 <b>Новое письмо</b>\n\n"
                        f"👤 <b>От:</b> {sender}\n"
                        f"📝 <b>Тема:</b> {subject}\n\n"
                        f"{body}"
                    )

                    try:
                        # Отправляем в Telegram
                        bot.send_message(CHAT_ID, text_message, parse_mode="HTML", disable_web_page_preview=True)
                        print(f"Письмо от {sender} отправлено в Telegram.")
                    except Exception as e:
                        print(f"Ошибка отправки в TG: {e}")
                        # Пробуем без HTML, если вдруг ошибка форматирования
                        bot.send_message(CHAT_ID, text_message.replace("<", "").replace(">", ""))

        mail.close()
        mail.logout()

    except Exception as e:
        print(f"Глобальная ошибка: {e}")

if __name__ == "__main__":
    check_email()
