import os
import requests
from gigachat import GigaChat

# ========== НАСТРОЙКИ ==========
# 1. Секретные данные из GitHub
WEATHER_KEY = os.environ.get("WEATHER_API_KEY")
GIGA_KEY = os.environ.get("GIGA_CREDENTIALS")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")  # Ваш ID из секретов

# 2. Токен бота (вручную)
TG_BOT_TOKEN = "ВСТАВИТЬ_ВАШ_ТОКЕН_СЮДА"

CITIES = {
    "Podolsk": {"lat": 55.4242, "lon": 37.5447, "name": "Подольск"},
    "Moscow":  {"lat": 55.7558, "lon": 37.6173, "name": "Москва"}
}
# ================================

def send_telegram(text):
    if not TG_CHAT_ID:
        print("Ошибка: Нет TG_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except Exception as e:
        print(f"Ошибка TG: {e}")

def get_weather_data(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/weather"
    params = {"lat": lat, "lon": lon, "appid": WEATHER_KEY, "units": "metric", "lang": "ru"}
    try:
        resp = requests.get(url, params=params)
        if resp.status_code == 200: return resp.json()
    except: pass
    return None

def parse_weather(data):
    if not data: return None
    return {
        "temp": round(data["main"]["temp"]),
        "feels": round(data["main"]["feels_like"]),
        "desc": data["weather"][0]["description"],
        "wind": round(data["wind"]["speed"]),
        "rain": "rain" in data
    }

def get_ai_advice(weather_p, weather_m):
    prompt = f"""
Я живу в Подольске, работаю в Москве.
Подольск: {weather_p['temp']}°C, {weather_p['desc']}, ветер {weather_p['wind']} м/с.
Москва: {weather_m['temp']}°C, {weather_m['desc']}, ветер {weather_m['wind']} м/с.
Дай короткий совет по одежде и зонту.
"""
    try:
        with GigaChat(credentials=GIGA_KEY, verify_ssl_certs=False) as giga:
            resp = giga.chat(prompt)
            return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Ошибка ИИ: {e}. Одевайся по погоде!"

def main():
    if not WEATHER_KEY or not GIGA_KEY:
        print("❌ Ошибка: Ключи API не найдены в секретах!")
        return

    w_pod = parse_weather(get_weather_data(CITIES["Podolsk"]["lat"], CITIES["Podolsk"]["lon"]))
    w_msk = parse_weather(get_weather_data(CITIES["Moscow"]["lat"], CITIES["Moscow"]["lon"]))

    if not w_pod or not w_msk:
        send_telegram("⚠️ Ошибка получения погоды.")
        return

    advice = get_ai_advice(w_pod, w_msk)
    rain = "☔" if (w_pod['rain'] or w_msk['rain']) else ""
    
    msg = f"""🌤️ <b>Утренний прогноз</b> {rain}
🏠 <b>Подольск:</b> {w_pod['temp']}°C ({w_pod['desc']})
🏢 <b>Москва:</b> {w_msk['temp']}°C ({w_msk['desc']})

💡 {advice}"""
    
    send_telegram(msg)

if __name__ == "__main__":
    main()
