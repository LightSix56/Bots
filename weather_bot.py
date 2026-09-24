"""Погодный бот: прогноз на сегодня и краткая сводка на завтра.

Источник — Open-Meteo, без API-ключей. Советы по одежде считаются скриптом,
без обращения к нейросетям: результат детерминирован и воспроизводим.

Важно про единицы измерения: API по умолчанию отдаёт скорость ветра в км/ч.
Запрос обязан содержать wind_speed_unit=ms, иначе порог «10 м/с» превращается
в «10 км/ч» и предупреждение о порывистом ветре срабатывает почти ежедневно.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import requests

import config
import storage
import tg

MSK = timezone(timedelta(hours=3))
API_URL = "https://api.open-meteo.com/v1/forecast"
STATE_PATH = os.environ.get("STATE_PATH", "data/state.json")

TOKEN_ENVS = ("WEATHER_TG_BOT_TOKEN",)
ALERT_COOLDOWN_HOURS = 6

CITIES = {
    "Podolsk": {"name": "Подольск", "icon": "🏠", "lat": 55.4242, "lon": 37.5447},
    "Moscow": {"name": "Москва", "icon": "🏢", "lat": 55.7558, "lon": 37.6173},
}

# Временные интервалы прогноза (подпись, диапазон часов МСК)
PERIODS: Sequence[tuple] = (
    ("06:00 – 10:00", range(6, 10)),
    ("10:00 – 14:00", range(10, 14)),
    ("14:00 – 18:00", range(14, 18)),
    ("18:00 – 22:00", range(18, 22)),
)

HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "weather_code",
)

WMO_CODES = {
    0: ("Ясно", "☀️"),
    1: ("В основном ясно", "🌤️"),
    2: ("Переменная облачность", "⛅"),
    3: ("Пасмурно", "☁️"),
    45: ("Туман", "🌫️"),
    48: ("Изморозь", "🌫️"),
    51: ("Легкая морось", "🌦️"),
    53: ("Морось", "🌦️"),
    55: ("Плотная морось", "🌧️"),
    56: ("Ледяная морось", "🌨️"),
    57: ("Сильная ледяная морось", "🌨️"),
    61: ("Небольшой дождь", "🌧️"),
    63: ("Умеренный дождь", "🌧️"),
    65: ("Сильный дождь", "🌧️"),
    66: ("Ледяной дождь", "🌨️"),
    67: ("Сильный ледяной дождь", "🌨️"),
    71: ("Небольшой снегопад", "❄️"),
    73: ("Снегопад", "❄️"),
    75: ("Сильный снегопад", "❄️"),
    77: ("Снежные зерна", "❄️"),
    80: ("Кратковременный дождь", "🌦️"),
    81: ("Ливень", "🌧️"),
    82: ("Сильный ливень", "⛈️"),
    85: ("Кратковременный снег", "🌨️"),
    86: ("Сильный снегопад", "🌨️"),
    95: ("Гроза", "⛈️"),
    96: ("Гроза с градом", "⛈️"),
    99: ("Сильная гроза с градом", "⛈️"),
}

# Приоритет кода погоды для периода: гроза > снег > дождь > морось > туман >
# облачность > ясность. Числовое значение кода WMO таким свойством не обладает:
# 45 (туман) больше 3 (пасмурно), а 51 (морось) больше 3, хотя морось заметнее.
CODE_TIERS = {
    0: 0, 1: 0, 2: 1, 3: 2,
    45: 3, 48: 3,
    51: 4, 53: 4, 55: 4,
    61: 5, 63: 5, 65: 5, 80: 5, 81: 5, 82: 5,
    56: 6, 57: 6, 66: 6, 67: 6, 71: 6, 73: 6, 75: 6, 77: 6, 85: 6, 86: 6,
    95: 7, 96: 7, 99: 7,
}

RAIN_CODES = frozenset({51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99})
SNOW_CODES = frozenset({56, 57, 66, 67, 71, 73, 75, 77, 85, 86})

POP_RAIN_THRESHOLD = 35
PRECIP_RAIN_THRESHOLD = 0.1
WIND_ALERT_THRESHOLD = 10.0
SWING_THRESHOLD = 8


@dataclass(frozen=True)
class HourPoint:
    time: datetime
    temp: float
    feels: float
    pop: Optional[float]
    precip: float
    wind: float
    code: Optional[int]


def decode_weather(code: Optional[int]) -> tuple:
    if code is None:
        return ("Нет данных", "🌡️")
    return WMO_CODES.get(code, ("Неизвестно", "🌡️"))


def build_request_params(lat: float, lon: float, *, days: int = 2) -> dict:
    """Параметры запроса к Open-Meteo.

    wind_speed_unit обязателен: без него скорость приходит в км/ч, а thresholds
    в коде заданы в м/с.
    """
    return {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "Europe/Moscow",
        "forecast_days": days,
        "wind_speed_unit": "ms",
    }


def _as_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK)
    return moment


def parse_hourly(hourly: dict) -> List[HourPoint]:
    """Разбирает блок hourly в отсортированный список точек.

    Часы без температуры отбрасываются: остальные поля по ним бессмысленны.
    """
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    points: List[HourPoint] = []
    for index, raw_time in enumerate(times):
        moment = _parse_time(raw_time)
        if moment is None:
            continue
        temp = _as_float(_at(hourly, "temperature_2m", index))
        if temp is None:
            continue
        feels = _as_float(_at(hourly, "apparent_temperature", index))
        points.append(
            HourPoint(
                time=moment,
                temp=temp,
                feels=temp if feels is None else feels,
                pop=_as_float(_at(hourly, "precipitation_probability", index)),
                precip=_as_float(_at(hourly, "precipitation", index)) or 0.0,
                wind=_as_float(_at(hourly, "wind_speed_10m", index)) or 0.0,
                code=_as_int(_at(hourly, "weather_code", index)),
            )
        )
    points.sort(key=lambda point: point.time)
    return points


def _at(hourly: dict, key: str, index: int):
    values = hourly.get(key)
    if not isinstance(values, (list, tuple)) or index >= len(values):
        return None
    return values[index]


def _as_int(value) -> Optional[int]:
    number = _as_float(value)
    return None if number is None else int(number)


def pick_representative(codes: Iterable[Optional[int]]) -> Optional[int]:
    """Выбирает код, представляющий период.

    Сначала по уровню значимости (гроза важнее снега, снег важнее дождя и т.д.),
    внутри уровня — самый частый код, при равенстве — более «тяжёлый» по коду.
    """
    known = [code for code in codes if code is not None]
    if not known:
        return None
    counts: Dict[int, int] = {}
    for code in known:
        counts[code] = counts.get(code, 0) + 1
    return max(counts, key=lambda code: (CODE_TIERS.get(code, 3), counts[code], code))


def periods_for_day(
    points: Sequence[HourPoint], *, day_offset: int, now: datetime
) -> Dict[str, dict]:
    """Агрегирует точки по интервалам указанного дня (0 — сегодня, 1 — завтра)."""
    target = (now + timedelta(days=day_offset)).date()
    by_hour = {point.time.hour: point for point in points if point.time.date() == target}

    result: Dict[str, dict] = {}
    for label, hours in PERIODS:
        chunk = [by_hour[hour] for hour in hours if hour in by_hour]
        if not chunk:
            continue
        temps = [point.temp for point in chunk]
        feels = [point.feels for point in chunk]
        pops = [point.pop for point in chunk if point.pop is not None]
        code = pick_representative(point.code for point in chunk)
        desc, icon = decode_weather(code)
        result[label] = {
            "min_temp": round(min(temps)),
            "max_temp": round(max(temps)),
            "min_feels": round(min(feels)),
            "max_feels": round(max(feels)),
            "pop": int(max(pops)) if pops else 0,
            "precip": round(sum(point.precip for point in chunk), 1),
            "wind": max(point.wind for point in chunk),
            "code": code,
            "desc": desc,
            "icon": icon,
        }
    return result


def summarize_day(
    points: Sequence[HourPoint], *, day_offset: int, now: datetime
) -> Optional[dict]:
    """Сводка за сутки: диапазон температур, осадки, представительный код."""
    target = (now + timedelta(days=day_offset)).date()
    chunk = [point for point in points if point.time.date() == target]
    if not chunk:
        return None
    pops = [point.pop for point in chunk if point.pop is not None]
    code = pick_representative(point.code for point in chunk)
    desc, icon = decode_weather(code)
    return {
        "min_temp": round(min(point.temp for point in chunk)),
        "max_temp": round(max(point.temp for point in chunk)),
        "min_feels": round(min(point.feels for point in chunk)),
        "max_feels": round(max(point.feels for point in chunk)),
        "pop": int(max(pops)) if pops else 0,
        "precip": round(sum(point.precip for point in chunk), 1),
        "wind": max(point.wind for point in chunk),
        "code": code,
        "desc": desc,
        "icon": icon,
    }


def current_snapshot(points: Sequence[HourPoint], now: datetime) -> Optional[dict]:
    """Ближайшая к текущему моменту точка (для строки «Сейчас»)."""
    same_day = [point for point in points if point.time.date() == now.date()]
    if not same_day:
        return None
    past = [point for point in same_day if point.time <= now]
    point = past[-1] if past else same_day[0]
    desc, icon = decode_weather(point.code)
    return {
        "temp": round(point.temp),
        "feels": round(point.feels),
        "wind": point.wind,
        "desc": desc,
        "icon": icon,
    }


def format_temp(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_temp_range(low: int, high: int) -> str:
    if low == high:
        return f"{format_temp(low)}°C"
    return f"{format_temp(low)}…{format_temp(high)}°C"


def _pick_clothes(min_feels: int) -> str:
    if min_feels < -15:
        return "Очень морозно. Тёплый пуховик, термобельё, шапка, шарф, перчатки и утеплённая обувь."
    if min_feels < -5:
        return "Мороз. Зимняя куртка или пуховик, шапка и перчатки."
    if min_feels < 4:
        return "Около нуля. Тёплая демисезонная куртка, шапка или капюшон, лёгкий шарф."
    if min_feels < 11:
        return "Прохладно. Демисезонная куртка / пальто, под низ свитер или плотное худи."
    if min_feels < 17:
        return "Умеренно. Ветровка, лёгкая куртка или плотная толстовка."
    if min_feels < 23:
        return "Тепло. Футболка или рубашка, лёгкие брюки/джинсы; на вечер можно накинуть лёгкую кофту."
    return "Жарко. Лёгкая летняя одежда, защита от солнца."


def generate_advice(
    city_name: str,
    periods: Dict[str, dict],
    tomorrow: Optional[dict] = None,
) -> List[str]:
    """Советы по городу на основе его собственного прогноза.

    Расчёт идёт по одному городу: раньше минимум и максимум брались по всем
    городам сразу, и «перепад» мог возникнуть между Москвой и Подольском,
    а не внутри дня.
    """
    blocks = list(periods.values()) + ([tomorrow] if tomorrow else [])
    if not blocks:
        return []

    feels = [value for block in blocks for value in (block["min_feels"], block["max_feels"])]
    min_feels, max_feels = min(feels), max(feels)

    has_rain = False
    has_snow = False
    rain_places: List[str] = []
    for label, block in periods.items():
        code = block.get("code")
        if code in RAIN_CODES or block["pop"] >= POP_RAIN_THRESHOLD or block["precip"] > PRECIP_RAIN_THRESHOLD:
            has_rain = True
            rain_places.append(f"{city_name} ({label})")
        if code in SNOW_CODES:
            has_snow = True

    clothes = _pick_clothes(min_feels)
    if max_feels - min_feels >= SWING_THRESHOLD:
        clothes += (
            f" Заметный перепад (от {format_temp(min_feels)}°C до {format_temp(max_feels)}°C)"
            " — одевайся многослойно."
        )

    if has_rain:
        places = ", ".join(dict.fromkeys(rain_places))
        umbrella = f"Обязательно возьми зонт ☔ (дождь: {places})."
    elif has_snow:
        umbrella = "Возможен снег ❄️ — лучше надеть непромокаемую обувь."
    else:
        umbrella = "Осадков не ожидается, зонт не нужен."

    advice = [f"<b>Одежда:</b> {clothes}", f"<b>Зонт:</b> {umbrella}"]

    max_wind = max(block["wind"] for block in blocks)
    if max_wind >= WIND_ALERT_THRESHOLD:
        advice.append(
            f"<b>Ветер:</b> порывы до {max_wind:.0f} м/с — выбери непродуваемую куртку с капюшоном."
        )
    return advice


def build_message(
    city_forecasts: Dict[str, dict],
    now: datetime,
    failures: Optional[Sequence[str]] = None,
) -> str:
    lines = [f"🌤 <b>Погода на {now.strftime('%d.%m.%Y')}</b> · обновлено {now.strftime('%H:%M')} МСК\n"]

    for forecast in city_forecasts.values():
        lines.append(f"{forecast['icon']} <b>{forecast['name']}:</b>")

        current = forecast.get("current")
        if current:
            lines.append(
                f"• Сейчас: {current['icon']} <b>{format_temp(current['temp'])}°C</b>"
                f" (ощущ. {format_temp(current['feels'])}°C), {current['desc']}"
            )

        for label, data in forecast["today"].items():
            line = (
                f"• {label}: {data['icon']} "
                f"<b>{format_temp_range(data['min_temp'], data['max_temp'])}</b>"
                f" (ощущ. {format_temp_range(data['min_feels'], data['max_feels'])}), {data['desc']}"
            )
            if data["pop"] >= 25 or data["precip"] > 0:
                line += f" 💧 {data['pop']}%"
            lines.append(line)

        tomorrow = forecast.get("tomorrow")
        if tomorrow:
            lines.append(
                f"• Завтра: {tomorrow['icon']} "
                f"{format_temp_range(tomorrow['min_temp'], tomorrow['max_temp'])}"
                f" (ощущ. {format_temp_range(tomorrow['min_feels'], tomorrow['max_feels'])}),"
                f" {tomorrow['desc']} 💧 {tomorrow['pop']}%"
            )

        advice = generate_advice(forecast["name"], forecast["today"], tomorrow)
        lines.append("")
        lines.append(f"💡 <b>Что надеть и взять — {forecast['name']}:</b>")
        lines.extend(f"• {item}" for item in advice)
        lines.append("")

    if failures:
        lines.append("⚠️ Не удалось получить прогноз: " + ", ".join(failures))

    return "\n".join(lines).strip()


def build_failure_message(failures: Sequence[str]) -> str:
    return "⚠️ Прогноз погоды недоступен. Города: " + ", ".join(failures)


def default_http_get(url: str, params: Optional[dict] = None, timeout: float = 20) -> dict:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _default_send(text: str) -> bool:
    token, chat_id = config.telegram_credentials(TOKEN_ENVS)
    return tg.send_message(text, token, chat_id, disable_preview=True)


def run(
    *,
    http_get: Optional[Callable] = None,
    send: Optional[Callable[[str], bool]] = None,
    send_alert: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
    check_mode: bool = False,
    state: Optional[dict] = None,
    state_path: str = STATE_PATH,
    cities: Optional[Dict[str, dict]] = None,
) -> bool:
    """Собирает прогноз и отправляет его. Возвращает True при успешной доставке.

    Отказ одного города не отменяет сводку по остальным: раньше первая же
    ошибка приводила к return и второй город не проверялся вовсе.
    """
    moment = (now or datetime.now(MSK)).astimezone(MSK)
    getter = http_get or default_http_get
    alert_sender = send_alert or send
    city_map = CITIES if cities is None else cities
    if state is None:
        state = storage.load_state(state_path)

    forecasts: Dict[str, dict] = {}
    failures: List[str] = []

    for key, info in city_map.items():
        print(f"📡 Запрос погоды: {info['name']}")
        try:
            payload = getter(API_URL, params=build_request_params(info["lat"], info["lon"], days=2))
            hourly = (payload or {}).get("hourly") or {}
            points = parse_hourly(hourly)
            if not points:
                raise ValueError("API вернул пустой прогноз")
            today = periods_for_day(points, day_offset=0, now=moment)
            if not today:
                raise ValueError("в прогнозе нет данных на сегодня")
            forecasts[key] = {
                "name": info["name"],
                "icon": info["icon"],
                "today": today,
                "tomorrow": summarize_day(points, day_offset=1, now=moment),
                "current": current_snapshot(points, moment),
            }
            print(f"✅ Данные получены: {info['name']}")
        except Exception as exc:
            failures.append(info["name"])
            print(f"❌ Ошибка прогноза для {info['name']}: {exc}")

    if not forecasts:
        message = build_failure_message(failures or list(city_map))
        print(message)
        if not check_mode and alert_sender is not None:
            _send_alert(alert_sender, state, state_path, "weather:all-failed", message, moment)
        return False

    text = build_message(forecasts, moment, failures)

    if check_mode:
        print(text)
        return True

    sender = send or _default_send
    try:
        delivered = bool(sender(text))
    except Exception as exc:
        print(f"❌ Не удалось отправить прогноз: {exc}")
        delivered = False

    return delivered


def _send_alert(sender, state, state_path, name, message, moment) -> None:
    """Шлёт уведомление об ошибке не чаще, чем раз в ALERT_COOLDOWN_HOURS."""
    if not storage.should_alert(state["alerts"], name, cooldown_hours=ALERT_COOLDOWN_HOURS, now=moment):
        print(f"ℹ️ Уведомление «{name}» подавлено (кулдаун)")
        return
    try:
        if sender(message):
            storage.record_alert(state["alerts"], name, now=moment)
            storage.save_state_atomic(state_path, state)
    except Exception as exc:
        print(f"❌ Не удалось отправить уведомление об ошибке: {exc}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    check_mode = "--check" in arguments or config.flag("CHECK_MODE")
    ok = run(check_mode=check_mode)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
