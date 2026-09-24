"""Настройка UTF-8 вывода в консоль.

Раньше перекодировка stdout/stderr была скопирована в начало каждого скрипта.
Здесь она в одном месте: на Windows консоль по умолчанию cp1251, и печать
эмодзи или кириллицы падает с UnicodeEncodeError, обрывая работу бота.
"""

from __future__ import annotations

import sys


def enable_utf8(stream=None) -> None:
    """Переключает поток на UTF-8 с заменой непечатаемых символов.

    Безопасно вызывать всегда: если у потока нет reconfigure (например, он
    перенаправлен в объект без этого метода), функция ничего не делает.
    errors="replace" гарантирует, что вывод не упадёт даже при экзотической
    локали: непредставимый символ заменится, а не выбросит исключение.
    """
    targets = [sys.stdout, sys.stderr] if stream is None else [stream]
    for target in targets:
        if target is None:
            continue
        reconfigure = getattr(target, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Поток уже закрыт или перенаправлен — печать не критична.
            continue


def safe_print(message: str, stream=None) -> None:
    """Печать, которая не роняет бота из-за кодировки консоли."""
    target = stream if stream is not None else sys.stdout
    try:
        print(message, file=target)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "ascii"
        print(message.encode(encoding, errors="replace").decode(encoding), file=target)


enable_utf8()
