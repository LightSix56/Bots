import os
import requests
from gigachat import GigaChat

# ========== НАСТРОЙКИ ==========
WEATHER_KEY = os.getenv("WEATHER_API_KEY")
GIGA_KEY = os.getenv("GIGA_CREDENTIALS")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# Координаты
CITIES = {
    "Podolsk": {"lat": 55.4242, "lon": 37.5447, "name": "Подольск"},
    "Moscow":  {"lat": 55.7558, "lon": 37.6173, "name": "Москва"}
}
# ================================

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Ошибка: Нет токенов TG")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"})

def get_weather_data(lat, lon):
    """Запрос к API погоды для одной точки"""
    url = f"https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": WEATHER_KEY,
        "units": "metric",
        "lang": "ru"
    }
    try:
        resp = requests.get(url, params=params)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None

def parse_weather(data):
    if not data: return None
    return {
        "temp": round(data["main"]["temp"]),
        "feels": round(data["main"]["feels_like"]),
        "desc": data["weather"][0]["description"],
        "wind": round(data["wind"]["speed"]),
        "rain": "rain" in data or "drizzle" in data.get("weather", [{}])[0].get("main", "").lower()
    }

def get_ai_advice(weather_p, weather_m):
    """Генерируем умный совет сразу для двух городов"""
    
    prompt = f"""
Ты личный ассистент. Я живу в Подольске, а работаю в Москве.
Дай мне совет на день, учитывая погоду в обоих городах.

Погода Подольск:
- Т: {weather_p['temp']}°C (ощущ. {weather_p['feels']}°C)
- {weather_p['desc']}, ветер {weather_p['wind']} м/с
- Дождь: {"есть" if weather_p['rain'] else "нет"}

Погода Москва:
- Т: {weather_m['temp']}°C (ощущ. {weather_m['feels']}°C)
- {weather_m['desc']}, ветер {weather_m['wind']} м/с
- Дождь: {"есть" if weather_m['rain'] else "нет"}

Задача:
1. Сравни погоду (где холоднее/ветренее).
2. Напиши, как одеться, чтобы было комфортно и там, и там.
3. Нужен ли зонт?
4. Пиши кратко (3-4 предложения), дружелюбно, без заумных фраз.
"""
    try:
        with GigaChat(credentials=GIGA_KEY, verify_ssl_certs=False) as giga:
            resp = giga.chat(prompt)
            return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Ошибка нейросети: {e}. Просто оденься по погоде!"

def main():
    print("🚀 Запуск двойного прогноза...")
    
    if not WEATHER_KEY:
        print("❌ Ошибка: Нет ключа погоды")
        return

    # 1. Получаем данные
    raw_podolsk = get_weather_data(CITIES["Podolsk"]["lat"], CITIES["Podolsk"]["lon"])
    raw_moscow = get_weather_data(CITIES["Moscow"]["lat"], CITIES["Moscow"]["lon"])

    w_pod = parse_weather(raw_podolsk)
    w_msk = parse_weather(raw_moscow)

    if not w_pod or not w_msk:
        send_telegram("⚠️ Ошибка получения данных о погоде (проверьте API ключ).")
        return

    # 2. Генерируем совет
    print("🤖 GigaChat анализирует разницу температур...")
    advice = get_ai_advice(w_pod, w_msk)

    # 3. Формируем отчет
    # Добавляем иконку зонта, если где-то дождь
    rain_alert = "☔" if (w_pod['rain'] or w_msk['rain']) else ""
    
    msg = f"""🌤️ <b>Утренний брифинг</b> {rain_alert}

🏠 <b>Подольск:</b> {w_pod['temp']}°C ({w_pod['desc']})
🏢 <b>Москва:</b>    {w_msk['temp']}°C ({w_msk['desc']})

💡 <b>Совет дня:</b>
{advice}
"""
    
    send_telegram(msg)
    print("✅ Готово!")

if __name__ == "__main__":
    main()
