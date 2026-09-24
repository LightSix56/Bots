"""Постоянное состояние ботов: что уже отправлено.

Файл состояния нужен, чтобы не слать одно и то же дважды. Запись атомарная:
прямая перезапись через open(..., "w") при обрыве процесса оставляет обрезанный
JSON, и бот теряет всю историю.

Формат (новый):
    {"seen": {"<hash>": "<ISO-8601 UTC>"}, "alerts": {"<имя>": "<ISO-8601 UTC>"}}

Старый формат news.py — плоский список хэшей — читается как совместимый,
записи получают текущее время.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Mapping, Optional


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> Optional[datetime]:
    """Разбор ISO-строки; None вместо исключения на мусоре."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def empty_state() -> Dict[str, Dict[str, str]]:
    return {"seen": {}, "alerts": {}}


def _coerce_seen(raw) -> Dict[str, str]:
    stamp = to_iso(now_utc())
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        # Старый формат seen.json — список хэшей без дат.
        return {str(item): stamp for item in raw}
    return {}


def load_state(path: str, *, now: Optional[datetime] = None) -> Dict[str, Dict[str, str]]:
    """Читает состояние. Отсутствующий или битый файл даёт пустое состояние."""
    _ = now
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        print(f"⚠️ Файл состояния {path} повреждён или недоступен — начинаем заново")
        return empty_state()

    if isinstance(raw, list):
        return {"seen": _coerce_seen(raw), "alerts": {}}
    if isinstance(raw, dict):
        state = empty_state()
        state["seen"] = _coerce_seen(raw.get("seen"))
        alerts = raw.get("alerts")
        if isinstance(alerts, dict):
            state["alerts"] = {str(k): str(v) for k, v in alerts.items()}
        return state
    return empty_state()


def save_state_atomic(path: str, state: Mapping[str, object]) -> None:
    """Пишет состояние через временный файл и os.replace (атомарно)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = {
        "seen": dict(state.get("seen") or {}),
        "alerts": dict(state.get("alerts") or {}),
    }
    fd, tmp_path = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def prune_seen(
    seen: Mapping[str, str],
    *,
    days: int,
    now: Optional[datetime] = None,
) -> Dict[str, str]:
    """Убирает записи старше days. Записи с нечитаемой датой сохраняются."""
    moment = now or now_utc()
    horizon = moment - timedelta(days=days)
    kept: Dict[str, str] = {}
    for key, stamp in seen.items():
        parsed = parse_iso(stamp)
        if parsed is None or parsed >= horizon:
            kept[key] = stamp
    return kept


def should_alert(
    alerts: Mapping[str, str],
    name: str,
    *,
    cooldown_hours: int,
    now: Optional[datetime] = None,
) -> bool:
    """Пора ли снова уведомлять об ошибке (защита от штормов и от циклов)."""
    moment = now or now_utc()
    last = parse_iso(alerts.get(name, ""))
    if last is None:
        return True
    return moment - last >= timedelta(hours=cooldown_hours)


def record_alert(alerts: Dict[str, str], name: str, *, now: Optional[datetime] = None) -> None:
    alerts[name] = to_iso(now or now_utc())


def add_seen(seen: Dict[str, str], keys: Iterable[str], *, now: Optional[datetime] = None) -> None:
    stamp = to_iso(now or now_utc())
    for key in keys:
        seen[str(key)] = stamp
