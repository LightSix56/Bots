import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import re
from bs4 import BeautifulSoup
import requests
from email.utils import parsedate_to_datetime

# Получаем данные из secrets
MAIL_USER = os.environ.get('MAIL_USER')
MAIL_PASS = os.environ.get('MAIL_PASS')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
TG_BOT_TOKEN = ('8337778471:AAEFoM9hZ7aWCxNkdJEMbA9I7CCn5j8KoiI')

# Настройки Mail.ru
IMAP_SERVER = 'imap.mail.ru'
IMAP_PORT = 993

def send_telegram_message(text):
    """Отправка сообщения в Telegram"""
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
    
    # Разбиваем длинные сообщения (макс 4096 символов)
    max_length = 4096
    if len(text) > max_length:
        parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
        for part in parts:
            payload = {
                'chat_id': TG_CHAT_ID,
                'text': part,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            }
            requests.post(url, json=payload)
    else:
        payload = {
            'chat_id': TG_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        response = requests.post(url, json=payload)
        return response.json()

def decode_mime_words(s):
    """Декодирование заголовков писем"""
    if s is None:
        return ""
    decoded_fragments = decode_header(s)
    fragments = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            if encoding:
                try:
                    fragments.append(fragment.decode(encoding))
                except:
                    fragments.append(fragment.decode('utf-8', errors='ignore'))
            else:
                fragments.append(fragment.decode('utf-8', errors='ignore'))
        else:
            fragments.append(str(fragment))
    return ''.join(fragments)

def extract_links_from_html(html_content):
    """Извлечение всех ссылок из HTML, включая скрытые в тексте"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Заменяем ссылки на текст с явным URL
    for link in soup.find_all('a'):
        href = link.get('href', '')
        text = link.get_text()
        if href:
            # Создаем HTML ссылку для Telegram
            link.replace_with(f'<a href="{href}">{text}</a>')
    
    return str(soup)

def get_email_body(msg):
    """Получение тела письма со всеми ссылками"""
    body = ""
    html_body = ""
    
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            if "attachment" not in content_disposition:
                if content_type == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        body = payload.decode(charset, errors='ignore')
                    except:
                        pass
                elif content_type == "text/html":
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        html_body = payload.decode(charset, errors='ignore')
                    except:
                        pass
    else:
        content_type = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or 'utf-8'
            if content_type == "text/html":
                html_body = payload.decode(charset, errors='ignore')
            else:
                body = payload.decode(charset, errors='ignore')
        except:
            pass
    
    # Приоритет HTML для извлечения ссылок
    if html_body:
        # Извлекаем текст и ссылки из HTML
        soup = BeautifulSoup(html_body, 'html.parser')
        
        # Формируем текст со ссылками
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.get_text(strip=True)
            if href and text:
                # Заменяем на Telegram HTML формат
                link.replace_with(f'<a href="{href}">{text}</a>')
            elif href:
                link.replace_with(f'<a href="{href}">{href}</a>')
        
        # Убираем лишние теги, оставляем только текст и ссылки
        for tag in soup.find_all():
            if tag.name not in ['a', 'b', 'strong', 'i', 'em', 'u', 'code', 'pre']:
                tag.unwrap()
        
        return soup.get_text(separator='\n', strip=True).replace('\n\n\n', '\n\n')
    
    return body

def check_mail():
    """Проверка почты и отправка писем от @mirea.ru"""
    try:
        # Подключение к Mail.ru
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(MAIL_USER, MAIL_PASS)
        mail.select('INBOX')
        
        # Получаем письма за последние 2 дня
        date_since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
        
        # Поиск писем
        status, messages = mail.search(None, f'(SINCE {date_since})')
        
        if status != 'OK':
            send_telegram_message("❌ Ошибка при поиске писем")
            return
        
        email_ids = messages[0].split()
        
        if not email_ids:
            send_telegram_message("📭 Новых писем от @mirea.ru не найдено")
            return
        
        found_mirea = False
        
        # Обрабатываем письма от новых к старым
        for email_id in reversed(email_ids):
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            
            if status != 'OK':
                continue
            
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Получаем отправителя
                    from_header = decode_mime_words(msg.get('From', ''))
                    
                    # Проверяем, что письмо от @mirea.ru
                    if '@mirea.ru' not in from_header.lower():
                        continue
                    
                    found_mirea = True
                    
                    # Получаем данные письма
                    subject = decode_mime_words(msg.get('Subject', 'Без темы'))
                    date_header = msg.get('Date')
                    
                    # Парсим дату
                    try:
                        email_date = parsedate_to_datetime(date_header)
                        # Конвертируем в МСК (UTC+3)
                        email_date_msk = email_date.astimezone()
                        date_str = email_date_msk.strftime("%d.%m.%Y %H:%M МСК")
                    except:
                        date_str = date_header or "Дата неизвестна"
                    
                    # Получаем тело письма
                    body = get_email_body(msg)
                    
                    # Формируем сообщение для Telegram
                    telegram_msg = f"""
📧 <b>Новое письмо от MIREA</b>

<b>От:</b> {from_header}
<b>Тема:</b> {subject}
<b>Дата:</b> {date_str}

<b>Текст письма:</b>
{body[:3000]}
"""
                    
                    if len(body) > 3000:
                        telegram_msg += "\n\n... (сообщение обрезано из-за длины)"
                    
                    # Отправляем в Telegram
                    send_telegram_message(telegram_msg)
        
        if not found_mirea:
            send_telegram_message("📭 Писем от @mirea.ru за последние 2 дня не найдено")
        
        mail.close()
        mail.logout()
        
    except Exception as e:
        error_msg = f"❌ Ошибка при проверке почты:\n{str(e)}"
        send_telegram_message(error_msg)
        print(error_msg)

if __name__ == "__main__":
    print("🤖 Запуск бота...")
    check_mail()
    print("✅ Проверка завершена")

