"""Почтовый бот: пересылает в Telegram письма от @mirea.ru из ящика Mail.ru.

Разбор ключевых решений:

* Поиск идёт по SINCE без UNSEEN. Раньше письмо, открытое в веб-интерфейсе или
  на телефоне, навсегда выпадало из обработки: флаг «прочитано» уже снят, а
  других следов у бота нет. Теперь письмо опознаётся по Message-ID, а состояние
  «уже отправлено» бот хранит сам.
* Письмо помечается прочитанным и заносится в историю только после успешной
  доставки в Telegram. Раньше при сбое отправки письмо всё равно помечалось
  прочитанным и больше не встречалось в поиске — потеря без возможности узнать.
* Тело забирается через BODY.PEEK[]: обычный BODY[]/RFC822 снимает флаг
  «прочитано» на сервере самим фактом чтения, что ломает повторные попытки.
* IMAP соединяется с таймаутом. Сокет без таймаута на зависшем сервере держит
  job GitHub Actions до 6 часов.
"""

from __future__ import annotations

import hashlib
import html
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from bs4 import BeautifulSoup

import config
import storage
import tg

MSK = timezone(timedelta(hours=3))
STATE_PATH = os.environ.get("STATE_PATH", "data/state.json")

IMAP_SERVER = "imap.mail.ru"
IMAP_PORT = 993
IMAP_TIMEOUT = 30
SEARCH_LOOKBACK_DAYS = 2

TOKEN_ENVS = ("MAIL_TG_BOT_TOKEN",)

MAX_BODY_LEN = 2500
MAX_LINKS = 15
ALERT_COOLDOWN_HOURS = 6
SEEN_TTL_DAYS = 120

MIREA_SENDER_RE = re.compile(r"@(?:[\w-]+\.)*mirea\.ru\b", re.IGNORECASE)

# Названия месяцев для IMAP. strftime("%b") зависит от локали: в русской локали
# он вернёт «янв», и сервер отвергнет критерий SINCE.
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def decode_mime_words(value: Optional[str]) -> str:
    """Декодирует MIME-заголовок (=?utf-8?B?...?=)."""
    if not value:
        return ""
    parts: List[str] = []
    for fragment, encoding in decode_header(value):
        if isinstance(fragment, bytes):
            for candidate in (encoding, "utf-8", "cp1251"):
                if not candidate:
                    continue
                try:
                    parts.append(fragment.decode(candidate))
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                parts.append(fragment.decode("utf-8", errors="replace"))
        else:
            parts.append(str(fragment))
    return "".join(parts)


def escape_html(text: str) -> str:
    return html.escape(text or "")


def is_mirea(sender: str) -> bool:
    """Письмо от домена mirea.ru (поддомены вроде edu.mirea.ru тоже подходят)."""
    return bool(MIREA_SENDER_RE.search(sender or ""))


def message_key(raw: bytes) -> str:
    """Стабильный идентификатор письма: Message-ID, иначе хэш содержимого."""
    msg = message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or "").strip()
    if message_id:
        return message_id
    return "sha1:" + hashlib.sha1(raw).hexdigest()


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="ignore")


def extract_body(raw: bytes) -> Tuple[str, List[dict]]:
    """Возвращает (обычный текст, список ссылок из HTML-части)."""
    msg = message_from_bytes(raw)
    plain = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition") or ""):
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain" and not plain:
                plain = _decode_part(part)
            elif content_type == "text/html" and not html_body:
                html_body = _decode_part(part)
    else:
        content_type = msg.get_content_type()
        decoded = _decode_part(msg)
        if content_type == "text/html":
            html_body = decoded
        else:
            plain = decoded

    if not html_body:
        return plain, []

    soup = BeautifulSoup(html_body, "html.parser")
    links: List[dict] = []
    seen_urls = set()
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href.startswith("http") or href in seen_urls:
            continue
        seen_urls.add(href)
        text = anchor.get_text(strip=True) or href
        links.append({"url": href, "text": text[:50]})
        if len(links) >= MAX_LINKS:
            break

    text = soup.get_text(separator="\n", strip=True)
    if not text.strip() and plain:
        text = plain
    return text, links


def get_email_body(raw: bytes) -> str:
    """Текст письма с блоком ссылок, готовым к вставке в HTML-сообщение."""
    text, links = extract_body(raw)
    if links:
        text += "\n\n🔗 <b>Ссылки в письме:</b>\n"
        for index, link in enumerate(links, 1):
            text += f'{index}. <a href="{escape_html(link["url"])}">{escape_html(link["text"])}</a>\n'
    return text


def _format_date(date_header: Optional[str]) -> str:
    if not date_header:
        return "Дата неизвестна"
    try:
        moment = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return date_header
    if moment is None:
        return date_header
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")


def build_telegram_message(raw: bytes, now: Optional[datetime] = None) -> str:
    """Собирает сообщение, экранируя текст письма, но сохраняя ссылки рабочими.

    Раньше экранировалось всё сообщение целиком вместе с уже собранным блоком
    <a href=...>, поэтому в Telegram вместо ссылки был виден её исходный код.
    """
    _ = now
    msg = message_from_bytes(raw)
    sender = decode_mime_words(msg.get("From", ""))
    subject = decode_mime_words(msg.get("Subject", "Без темы"))
    text, links = extract_body(raw)

    truncated = ""
    if len(text) > MAX_BODY_LEN:
        text = text[:MAX_BODY_LEN]
        truncated = "\n\n… (письмо обрезано из-за длины)"

    lines = [
        "📧 <b>Новое письмо от MIREA</b>",
        "",
        f"<b>От:</b> {escape_html(sender)}",
        f"<b>Тема:</b> {escape_html(subject)}",
        f"<b>Дата:</b> {escape_html(_format_date(msg.get('Date')))}",
        "",
        "<b>Текст письма:</b>",
        escape_html(text) + truncated,
    ]

    if links:
        lines.append("")
        lines.append("🔗 <b>Ссылки в письме:</b>")
        for index, link in enumerate(links, 1):
            lines.append(
                f'{index}. <a href="{escape_html(link["url"])}">'
                f'{escape_html(link["text"])}</a>'
            )

    return "\n".join(lines)


class ImapMailbox:
    """Тонкая обёртка над imaplib с таймаутом и чтением через BODY.PEEK[]."""

    def __init__(self, host: str = IMAP_SERVER, port: int = IMAP_PORT, timeout: float = IMAP_TIMEOUT):
        self._conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)

    def login(self, user: str, password: str):
        self._conn.login(user, password)

    def select(self, folder: str = "INBOX"):
        return self._conn.select(folder)

    def search(self, criteria: str):
        return self._conn.search(None, criteria)

    def fetch_raw(self, msg_id: str) -> bytes:
        status, data = self._conn.fetch(msg_id, "(BODY.PEEK[])")
        if status != "OK":
            raise IOError(f"IMAP fetch вернул статус {status}")
        for part in data or []:
            if isinstance(part, tuple) and len(part) > 1:
                return part[1]
        raise IOError("IMAP fetch не вернул тело письма")

    def is_seen(self, msg_id: str) -> bool:
        """Уже помечено ли письмо прочитанным на сервере.

        Поиск идёт без UNSEEN, поэтому на первом запуске (история пуста) нужно
        отличить старую прочитанную переписку от новых писем, иначе бот
        перешлёт всё, что попало в окно поиска.
        """
        status, data = self._conn.fetch(msg_id, "(FLAGS)")
        if status != "OK":
            raise IOError(f"IMAP fetch FLAGS вернул статус {status}")
        for part in data or []:
            if isinstance(part, bytes) and b"\\Seen" in part:
                return True
            if isinstance(part, tuple) and part and isinstance(part[0], bytes):
                if b"\\Seen" in part[0]:
                    return True
        return False

    def mark_seen(self, msg_id: str):
        self._conn.store(msg_id, "+FLAGS", "\\Seen")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            self._conn.logout()
        except Exception:
            pass


def search_criteria(now: datetime, *, lookback_days: int = SEARCH_LOOKBACK_DAYS) -> str:
    """Критерий поиска без UNSEEN: прочитанное в другом клиенте тоже нужно.

    Дата формируется вручную, а не через strftime("%b"): тот зависит от локали
    и в русской раскладке даёт «янв» вместо «Jan», что сервер не примет.
    """
    since = now - timedelta(days=lookback_days)
    stamp = f"{since.day:02d}-{IMAP_MONTHS[since.month - 1]}-{since.year}"
    return f"(SINCE {stamp})"


def _iter_message_ids(search_result) -> List[str]:
    if not search_result:
        return []
    _status, data = search_result
    if not data:
        return []
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    return [token for token in str(raw).split() if token]


def _default_send(text: str) -> bool:
    token, chat_id = config.telegram_credentials(TOKEN_ENVS)
    return tg.send_message(text, token, chat_id, disable_preview=False)


def run(
    *,
    imap_factory: Optional[Callable] = None,
    send: Optional[Callable[[str], bool]] = None,
    send_alert: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
    check_mode: bool = False,
    state: Optional[dict] = None,
    state_path: str = STATE_PATH,
    env: Optional[Mapping[str, str]] = None,
    initialise: Optional[bool] = None,
) -> bool:
    """Проверяет ящик и пересылает новые письма от MIREA.

    True — обработка прошла без потерь (в том числе когда писем нет).

    initialise управляет первым запуском. При True старые письма, уже помеченные
    прочитанными на сервере, запоминаются, но не пересылаются: иначе переход на
    дедупликацию по Message-ID вызвал бы залп всей переписки за окно поиска.
    По умолчанию True только при отсутствии файла состояния — дальше решение
    принимается исключительно по Message-ID, и письмо, прочитанное на телефоне,
    всё равно будет переслано.
    """
    moment = (now or datetime.now(MSK)).astimezone(MSK)
    source = os.environ if env is None else env
    if state is None:
        state = storage.load_state(state_path)
        if initialise is None:
            initialise = not os.path.exists(state_path)
    if initialise is None:
        initialise = False
    if initialise:
        print("🆕 Первый запуск: старые прочитанные письма будут только запомнены")

    user = (source.get("MAIL_USER") or "").strip()
    password = (source.get("MAIL_PASS") or "").strip()
    if not user or not password:
        print("❌ Не заданы MAIL_USER / MAIL_PASS")
        return False

    factory = imap_factory or ImapMailbox
    mailbox = None
    try:
        print("📨 Подключение к Mail.ru")
        mailbox = factory(IMAP_SERVER, IMAP_PORT, IMAP_TIMEOUT)
        mailbox.login(user, password)
        mailbox.select("INBOX")

        criteria = search_criteria(moment)
        print(f"🔍 Поиск писем: {criteria}")
        ids = _iter_message_ids(mailbox.search(criteria))
        print(f"📬 Писем в выборке: {len(ids)}")
        if not ids:
            return True

        all_delivered = True
        processed = 0
        remembered = 0

        for msg_id in reversed(ids):  # от новых к старым
            try:
                raw = mailbox.fetch_raw(msg_id)
            except Exception as exc:
                print(f"❌ Не удалось прочитать письмо {msg_id}: {exc}")
                all_delivered = False
                continue

            key = message_key(raw)
            if key in state["seen"]:
                continue

            msg = message_from_bytes(raw)
            sender = decode_mime_words(msg.get("From", ""))
            if not is_mirea(sender):
                continue

            # Флаг \Seen учитывается ТОЛЬКО при первичной инициализации. Дальше
            # решение принимается по Message-ID: иначе письмо, прочитанное на
            # телефоне или в веб-интерфейсе, снова выпадало бы из обработки.
            if initialise:
                checker = getattr(mailbox, "is_seen", None)
                if checker is not None:
                    try:
                        already_read = checker(msg_id)
                    except Exception as exc:
                        print(f"⚠️ Не удалось узнать флаг письма {msg_id}: {exc}")
                        already_read = False
                    if already_read:
                        if not check_mode:
                            storage.add_seen(state["seen"], [key], now=moment)
                            remembered += 1
                        print(f"↩️ Письмо {msg_id} уже прочитано ранее — пропускаем")
                        continue

            subject = decode_mime_words(msg.get("Subject", "Без темы"))
            print(f"✅ Письмо от MIREA: {subject}")
            processed += 1

            if check_mode:
                print(build_telegram_message(raw, moment))
                continue

            sender_fn = send or _default_send
            try:
                delivered = bool(sender_fn(build_telegram_message(raw, moment)))
            except Exception as exc:
                print(f"❌ Ошибка отправки письма {msg_id}: {exc}")
                delivered = False

            if not delivered:
                # Ни флага, ни записи в истории: письмо вернётся в следующий запуск.
                print(f"⚠️ Письмо {msg_id} не доставлено — оставляем непрочитанным")
                all_delivered = False
                continue

            # Порядок важен. Сначала фиксируем факт доставки на диске, потом
            # помечаем письмо прочитанным. Обратный порядок при сбое между
            # шагами дал бы письмо, помеченное прочитанным, но не отправленное, —
            # и оно потерялось бы безвозвратно, как в прежней версии.
            storage.add_seen(state["seen"], [key], now=moment)
            state["seen"] = storage.prune_seen(state["seen"], days=SEEN_TTL_DAYS, now=moment)
            try:
                storage.save_state_atomic(state_path, state)
            except OSError as exc:
                print(f"❌ Не удалось сохранить историю писем: {exc}")
                all_delivered = False

            try:
                mailbox.mark_seen(msg_id)
            except Exception as exc:
                print(f"⚠️ Не удалось пометить письмо {msg_id} прочитанным: {exc}")

        # Внутри цикла состояние сохраняется после каждого доставленного письма.
        # Здесь досохраняем только то, что было лишь помечено при инициализации.
        if remembered and not check_mode:
            try:
                state["seen"] = storage.prune_seen(state["seen"], days=SEEN_TTL_DAYS, now=moment)
                storage.save_state_atomic(state_path, state)
                print(f"💾 История писем: {len(state['seen'])} записей")
            except OSError as exc:
                print(f"❌ Не удалось сохранить историю писем: {exc}")
                all_delivered = False
        elif processed and not check_mode:
            print(f"💾 История писем: {len(state['seen'])} записей")

        if not processed and not remembered:
            print("ℹ️ Новых писем от MIREA нет")

        return all_delivered

    except Exception as exc:
        message = f"❌ Ошибка при проверке почты: {exc}"
        print(message)
        if not check_mode:
            _send_alert(
                send_alert or send, state, state_path, "mail:check-failed", message, moment
            )
        return False
    finally:
        if mailbox is not None:
            try:
                mailbox.close()
            except Exception:
                pass


def _send_alert(sender, state, state_path, name, message, moment) -> None:
    if sender is None:
        return
    if not storage.should_alert(state["alerts"], name, cooldown_hours=ALERT_COOLDOWN_HOURS, now=moment):
        print(f"ℹ️ Уведомление «{name}» подавлено (кулдаун)")
        return
    try:
        if sender(message):
            storage.record_alert(state["alerts"], name, now=moment)
            storage.save_state_atomic(state_path, state)
    except Exception as exc:
        print(f"❌ Не удалось отправить уведомление: {exc}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    check_mode = "--check" in arguments or config.flag("CHECK_MODE")
    return 0 if run(check_mode=check_mode) else 1


if __name__ == "__main__":
    raise SystemExit(main())
