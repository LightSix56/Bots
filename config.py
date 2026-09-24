"""Чтение настроек из окружения.

Токены ботов разных назначений, поэтому у каждого workflow своя переменная.
Дополнительно поддерживаются исторические имена (TELEGRAM_TOKEN, TG_BOT_TOKEN),
чтобы старые секреты продолжали работать.

chat_id обязателен везде. Раньше в bot.py был жёстко зашит запасной chat_id:
при незаданном секрете письма уходили в посторонний чат вместо явной ошибки.
"""

from __future__ import annotations

import os
from typing import Iterable, Mapping, Optional, Sequence, Tuple

TOKEN_ALIASES: Tuple[str, ...] = ("TELEGRAM_TOKEN", "TG_BOT_TOKEN")
CHAT_ID_ENVS: Tuple[str, ...] = ("TG_CHAT_ID",)


class ConfigError(RuntimeError):
    """Обязательная настройка отсутствует."""


def first_set(env: Mapping[str, str], names: Iterable[str]) -> Optional[str]:
    """Первое непустое значение из перечисленных переменных окружения."""
    for name in names:
        value = env.get(name)
        if value and value.strip():
            return value.strip()
    return None


def telegram_credentials(
    token_envs: Sequence[str],
    *,
    env: Optional[Mapping[str, str]] = None,
    require: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (token, chat_id).

    require=False используется в режиме самопроверки, где вывод идёт в консоль
    и секреты не нужны.
    """
    source = os.environ if env is None else env
    token = first_set(source, tuple(token_envs) + TOKEN_ALIASES)
    chat_id = first_set(source, CHAT_ID_ENVS)

    if not require:
        return token, chat_id

    missing = []
    if not token:
        missing.append("/".join(token_envs))
    if not chat_id:
        missing.append("/".join(CHAT_ID_ENVS))
    if missing:
        raise ConfigError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing)
        )
    return token, chat_id


def flag(name: str, *, env: Optional[Mapping[str, str]] = None, default: bool = False) -> bool:
    """Логический флаг из окружения: 1/true/yes/on."""
    source = os.environ if env is None else env
    value = (source.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "да"}
