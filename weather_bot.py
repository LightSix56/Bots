import os
import sys
import requests

# Корректный вывод UTF-8 в консоль при любых локалях (включая Windows cp1251)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Настройки Telegram (поддержка как новых, так и старых названий секретов)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or os.environ.get("WEATHER_TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or os.environ.get("WEATHER_TG_CHAT_ID")

CITIES = {
    "Podolsk": {
        "name": "Подольск",
        "icon": "🏠",
        "lat": 55.4242,
        "lon": 37.5447
    },
    "Moscow": {
        "name": "Москва",
        "icon": "🏢",
        "lat": 55.7558,
        "lon": 37.6173
    }
}

# Временные срезы прогноза (часы в сутках МСК)
PERIODS = [
    ("Утро", 8),
    ("День", 14),
    ("Вечер", 20)
]

# Таблица кодов погоды WMO
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

def decode_weather(code: int) -> tuple:
    return WMO_CODES.get(code, ("Неизвестно", "🌡️"))

def send_telegram(text: str):
    """Отправка сообщения в Telegram с поддержкой HTML."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ TG_BOT_TOKEN или TG_CHAT_ID не заданы. Вывод в консоль:")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        res_json = resp.json()
        if res_json.get("ok"):
            print("✅ Сообщение успешно отправлено в Telegram")
        else:
            print(f"❌ Ошибка Telegram API: {res_json.get('description')}")
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")

def get_forecast(lat: float, lon: float) -> dict:
    """Получение почасового прогноза на сегодня через Open-Meteo (без API-ключей)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,precipitation,weathercode,windspeed_10m",
        "timezone": "Europe/Moscow",
        "forecast_days": 1
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()["hourly"]

def extract_city_periods(hourly_data: dict) -> dict:
    """Извлекает данные для утра (08:00), дня (14:00) и вечера (20:00)."""
    periods_data = {}
    for name, hour in PERIODS:
        w_text, icon = decode_weather(hourly_data["weathercode"][hour])
        periods_data[name] = {
            "temp": round(hourly_data["temperature_2m"][hour]),
            "feels": round(hourly_data["apparent_temperature"][hour]),
            "pop": hourly_data["precipitation_probability"][hour],
            "precip": hourly_data["precipitation"][hour],
            "wind": round(hourly_data["windspeed_10m"][hour]),
            "code": hourly_data["weathercode"][hour],
            "desc": w_text,
            "icon": icon,
            "hour": hour
        }
    return periods_data

def generate_advice(city_forecasts: dict) -> list:
    """Скриптовый подбор одежды и вещей без использования нейросетей."""
    all_feels = []
    all_winds = []
    has_rain = False
    has_snow = False
    rain_places = []

    for city_key, periods in city_forecasts.items():
        city_name = CITIES[city_key]["name"]
        for p_name, p_data in periods.items():
            all_feels.append(p_data["feels"])
            all_winds.append(p_data["wind"])
            code = p_data["code"]
            precip = p_data["precip"]
            pop = p_data["pop"]

            is_rain = (code in (51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99)) or (pop >= 35) or (precip > 0.1)
            is_snow = (code in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86))

            if is_rain:
                has_rain = True
                rain_places.append(f"{city_name} ({p_name.lower()})")
            if is_snow:
                has_snow = True

    min_feels = min(all_feels)
    max_feels = max(all_feels)
    max_wind = max(all_winds)

    # 1. Рекомендация по одежде на основе ощущаемой температуры
    if min_feels < -15:
        clothes = "Очень морозно. Тёплый пуховик, термобельё, шапка, шарф, перчатки и утеплённая обувь."
    elif min_feels < -5:
        clothes = "Мороз. Зимняя куртка или пуховик, шапка и перчатки."
    elif min_feels < 4:
        clothes = "Около нуля. Тёплая демисезонная куртка, шапка или капюшон, лёгкий шарф."
    elif min_feels < 11:
        clothes = "Прохладно. Демисезонная куртка / пальто, под низ свитер или плотное худи."
    elif min_feels < 17:
        clothes = "Умеренно. Ветровка, лёгкая куртка или плотная толстовка."
    elif min_feels < 23:
        clothes = "Тепло. Футболка или рубашка, лёгкие брюки/джинсы; на вечер можно накинуть лёгкую кофту."
    else:
        clothes = "Жарко. Лёгкая летняя одежда, защита от солнца."

    # Если большой суточный перепад
    if max_feels - min_feels >= 8:
        clothes += f" Заметный перепад (от {min_feels}°C до {max_feels}°C) — одевайся многослойно."

    # 2. Зонт / осадки
    if has_rain:
        details = ", ".join(dict.fromkeys(rain_places))
        umbrella = f"Обязательно возьми зонт ☔ (дождь: {details})."
    elif has_snow:
        umbrella = "Возможен снег ❄️ — лучше надеть непромокаемую обувь."
    else:
        umbrella = "Осадков не ожидается, зонт не нужен."

    advice_items = [
        f"<b>Одежда:</b> {clothes}",
        f"<b>Зонт:</b> {umbrella}"
    ]

    # 3. Ветер
    if max_wind >= 10:
        advice_items.append(f"<b>Ветер:</b> Порывистый до {max_wind} м/с — выбери непродуваемую куртку с капюшоном.")

    return advice_items

def format_temp(val: int) -> str:
    return f"+{val}" if val > 0 else str(val)

def build_message(city_forecasts: dict, advice: list) -> str:
    """Формирует итоговое красивое сообщение для Telegram."""
    lines = ["🌤 <b>Прогноз погоды на сегодня</b>\n"]

    for city_key, periods in city_forecasts.items():
        city_info = CITIES[city_key]
        lines.append(f"{city_info['icon']} <b>{city_info['name']}:</b>")
        for p_name, data in periods.items():
            t = format_temp(data['temp'])
            f = format_temp(data['feels'])
            line = f"• {p_name} ({data['hour']:02d}:00): {data['icon']} <b>{t}°C</b> (ощущ. {f}°C), {data['desc']}"
            if data['pop'] >= 25 or data['precip'] > 0:
                line += f" 💧 {data['pop']}%"
            lines.append(line)
        lines.append("")

    lines.append("💡 <b>Что надеть и взять:</b>")
    for item in advice:
        lines.append(f"• {item}")

    return "\n".join(lines)

def main():
    print("🚀 Получение прогноза погоды...")
    city_forecasts = {}

    for city_key, city_data in CITIES.items():
        try:
            hourly = get_forecast(city_data["lat"], city_data["lon"])
            city_forecasts[city_key] = extract_city_periods(hourly)
            print(f"✅ Данные получены для: {city_data['name']}")
        except Exception as e:
            print(f"❌ Ошибка получения погоды для {city_data['name']}: {e}")
            send_telegram(f"⚠️ Ошибка получения прогноза для {city_data['name']}.")
            return

    advice = generate_advice(city_forecasts)
    message = build_message(city_forecasts, advice)
    send_telegram(message)

if __name__ == "__main__":
    main()
