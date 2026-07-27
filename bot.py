import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import re
from bs4 import BeautifulSoup
import requests
from email.utils import parsedate_to_datetime
import html

# Получаем данные из secrets
MAIL_USER = os.environ.get('MAIL_USER')
MAIL_PASS = os.environ.get('MAIL_PASS')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '962277709')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN')

# Настройки Mail.ru
IMAP_SERVER = 'imap.mail.ru'
IMAP_PORT = 993

def send_telegram_message(text, use_html=True):
    """Отправка сообщения в Telegram с обработкой ошибок"""
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
    
    # Разбиваем длинные сообщения (макс 4096 символов)
    max_length = 4096
    parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
    
    for part in parts:
        payload = {
            'chat_id': TG_CHAT_ID,
            'text': part,
            'parse_mode': 'HTML' if use_html else None,
            'disable_web_page_preview': False
        }
        
        try:
            response = requests.post(url, json=payload, timeout=15)
            result = response.json()
            
            if not result.get('ok'):
                print(f"⚠️ Ошибка Telegram API: {result.get('description')}")
                
                # Если ошибка парсинга HTML - пробуем без форматирования
                if result.get('error_code') == 400 and use_html:
                    print("🔁 Повторная отправка без HTML...")
                    payload['parse_mode'] = None
                    payload['text'] = part
                    response2 = requests.post(url, json=payload, timeout=15)
                    result2 = response2.json()
                    if result2.get('ok'):
                        print("✅ Отправлено без HTML форматирования")
                    else:
                        print(f"❌ Не удалось отправить: {result2.get('description')}")
            else:
                print("✅ Сообщение успешно отправлено")
                
        except Exception as e:
            print(f"❌ Ошибка при отправке: {e}")

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

def escape_html(text):
    """Экранирование HTML символов для безопасной отправки в Telegram"""
    return html.escape(text)

def get_email_body(msg):
    """Получение тела письма"""
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
    
    # Извлекаем текст и ссылки из HTML
    if html_body:
        soup = BeautifulSoup(html_body, 'html.parser')
        
        # Извлекаем все ссылки
        links = []
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.get_text(strip=True)
            if href and href.startswith('http'):
                links.append({'url': href, 'text': text if text else href})
        
        # Получаем чистый текст
        text = soup.get_text(separator='\n', strip=True)
        
        # Добавляем ссылки в конец
        if links:
            text += '\n\n🔗 <b>Ссылки в письме:</b>\n'
            for idx, link_data in enumerate(links, 1):
                link_text = escape_html(link_data['text'][:50])
                link_url = link_data['url']
                text += f'{idx}. <a href="{link_url}">{link_text}</a>\n'
        
        return text
    
    return body

def check_mail():
    """Проверка почты и отправка писем от @mirea.ru"""
    try:
        print("📨 Подключение к Mail.ru...")
        # Подключение к Mail.ru
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(MAIL_USER, MAIL_PASS)
        mail.select('INBOX')
        print("✅ Подключение успешно")
        
        # Получаем письма только за ВЧЕРА и СЕГОДНЯ
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        print(f"🔍 Поиск НЕПРОЧИТАННЫХ писем с {yesterday}...")
        
        # Поиск ТОЛЬКО непрочитанных писем за последние 2 дня
        status, messages = mail.search(None, f'(UNSEEN SINCE {yesterday})')
        
        if status != 'OK':
            send_telegram_message("❌ Ошибка при поиске писем", use_html=False)
            return
        
        email_ids = messages[0].split()
        print(f"📬 Найдено {len(email_ids)} непрочитанных писем")
        
        if not email_ids:
            print("📭 Новых непрочитанных писем не найдено")
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
                    
                    print(f"📧 Проверяю письмо от: {from_header}")
                    
                    # Проверяем, что письмо от @mirea.ru
                    if '@mirea.ru' not in from_header.lower():
                        continue
                    
                    print(f"✅ Найдено письмо от MIREA!")
                    found_mirea = True
                    
                    # Получаем данные письма
                    subject = decode_mime_words(msg.get('Subject', 'Без темы'))
                    date_header = msg.get('Date')
                    
                    # Парсим дату
                    try:
                        email_date = parsedate_to_datetime(date_header)
                        email_date_msk = email_date.astimezone()
                        date_str = email_date_msk.strftime("%d.%m.%Y %H:%M МСК")
                    except:
                        date_str = date_header or "Дата неизвестна"
                    
                    # Получаем тело письма
                    body = get_email_body(msg)
                    
                    # Экранируем HTML символы в заголовках
                    from_safe = escape_html(from_header)
                    subject_safe = escape_html(subject)
                    
                    # Формируем сообщение для Telegram (безопасно)
                    telegram_msg = f"""📧 <b>Новое письмо от MIREA</b>

<b>От:</b> {from_safe}
<b>Тема:</b> {subject_safe}
<b>Дата:</b> {date_str}

<b>Текст письма:</b>
{escape_html(body[:2500])}
"""
                    
                    if len(body) > 2500:
                        telegram_msg += "\n\n... (сообщение обрезано из-за длины)"
                    
                    print("📤 Отправляю в Telegram...")
                    # Отправляем в Telegram
                    send_telegram_message(telegram_msg)
                    
                    # ВАЖНО: Помечаем письмо как прочитанное
                    mail.store(email_id, '+FLAGS', '\\Seen')
                    print(f"✅ Письмо {email_id.decode()} помечено как прочитанное")
        
        if not found_mirea:
            print("ℹ️ Непрочитанных писем от @mirea.ru не найдено")
        
        mail.close()
        mail.logout()
        
    except Exception as e:
        error_msg = f"❌ Ошибка при проверке почты:\n{str(e)}"
        send_telegram_message(error_msg, use_html=False)
        print(error_msg)

if __name__ == "__main__":
    print("🤖 Запуск бота...")
    check_mail()
    print("✅ Проверка завершена")
