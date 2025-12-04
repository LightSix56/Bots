import imaplib
import email
from email.header import decode_header
import telebot
from bs4 import BeautifulSoup
import re

# ================= НАСТРОЙКИ =================
# Данные от почты (Пример для Yandex/Mail.ru/Gmail)
EMAIL_USER = "ваш_email@yandex.ru"
EMAIL_PASS = "ваш_пароль_приложения"  # Пароль приложения, не от входа!
IMAP_SERVER = "imap.yandex.ru"        # imap.gmail.com, imap.mail.ru и т.д.

# Данные Telegram
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"
CHAT_ID = "ВАШ_CHAT_ID"               # ID вашего чата (куда слать)

# ================= ЛОГИКА =================

def clean_text(text):
    """Убирает лишние пробелы и пустые строки"""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(filter(None, lines))

def get_email_body_with_links(msg):
    """
    Извлекает текст и ссылки из письма.
    Превращает HTML-ссылки <a href="url">Текст</a> в формат "Текст: [ url ]"
    """
    body = ""
    html_content = None

    # 1. Ищем HTML и Текстовую версии
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if not payload: continue
                charset = part.get_content_charset() or 'utf-8'
                decoded_part = payload.decode(charset, errors="ignore")
                
                if content_type == "text/html":
                    html_content = decoded_part
                elif content_type == "text/plain" and html_content is None:
                    body = decoded_part
            except Exception as e:
                print(f"Ошибка при декодировании части письма: {e}")
    else:
        # Не multipart
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or 'utf-8'
            decoded_part = payload.decode(charset, errors="ignore")
            
            if msg.get_content_type() == "text/html":
                html_content = decoded_part
            else:
                body = decoded_part
        except Exception as e:
            print(f"Ошибка при чтении простого письма: {e}")

    # 2. Если есть HTML, достаем из него ссылки и текст
    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Находим все ссылки и переделываем их вид
        for a in soup.find_all('a', href=True):
            url = a['href']
            text = a.get_text(strip=True)
            if url and text:
                # Заменяем ссылку на текст вида: "Кнопка: [ ссылка ]"
                new_string = f" {text}: [ {url} ] "
                a.replace_with(new_string)
            elif url:
                a.replace_with(f" [ {url} ] ")
        
        # Получаем текст без HTML тегов
        body = soup.get_text(separator="\n")

    return clean_text(body)

def check_email():
    bot = telebot.TeleBot(BOT_TOKEN)

    try:
        # Подключение к почте
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # Поиск НЕПРОЧИТАННЫХ писем (UNSEEN)
        status, messages = mail.search(None, 'UNSEEN')
        
        email_ids = messages[0].split()
        
        if not email_ids:
            print("Нет новых писем.")
            return

        print(f"Найдено {len(email_ids)} новых писем.")

        # Обрабатываем каждое письмо
        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Декодируем тему
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                    
                    # Декодируем отправителя
                    sender, encoding = decode_header(msg["From"])[0]
                    if isinstance(sender, bytes):
                        sender = sender.decode(encoding if encoding else "utf-8", errors="ignore")

                    # Получаем текст с ссылками
                    body = get_email_body_with_links(msg)

                    # Формируем сообщение для Telegram
                    # Ограничим длину сообщения (Telegram не любит > 4096 символов)
                    if len(body) > 3000:
                        body = body[:3000] + "\n... (письмо обрезано)"

                    text_to_send = (
                        f"📩 <b>Новое письмо!</b>\n\n"
                        f"<b>От:</b> {sender}\n"
                        f"<b>Тема:</b> {subject}\n\n"
                        f"{body}"
                    )

                    try:
                        bot.send_message(CHAT_ID, text_to_send, parse_mode="HTML", disable_web_page_preview=True)
                        print(f"Отправлено письмо от {sender}")
                    except Exception as e:
                        print(f"Ошибка отправки в ТГ: {e}")
                        # Если ошибка HTML (редко, но бывает из-за спецсимволов), шлем без форматирования
                        bot.send_message(CHAT_ID, text_to_send.replace("<", "&lt;").replace(">", "&gt;"))

        mail.close()
        mail.logout()

    except Exception as e:
        print(f"Ошибка при проверке почты: {e}")

if __name__ == "__main__":
    check_email()
