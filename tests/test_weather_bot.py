"""Тесты погодного бота: единицы ветра, агрегация, советы, устойчивость."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import storage
import weather_bot as wb

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 1, 31, 3, 53, tzinfo=MSK)


def make_hours(temps=None, codes=None, winds=None, pops=None, precips=None,
               date="2026-01-31", start=0):
    """Собирает ответ Open-Meteo: 24 часа одного дня."""
    temps = temps if temps is not None else [0] * 24
    codes = codes if codes is not None else [0] * 24
    winds = winds if winds is not None else [3] * 24
    pops = pops if pops is not None else [0] * 24
    precips = precips if precips is not None else [0.0] * 24
    return {
        "time": [f"{date}T{h:02d}:00" for h in range(start, start + 24)],
        "temperature_2m": temps,
        "apparent_temperature": temps,
        "precipitation_probability": pops,
        "precipitation": precips,
        "wind_speed_10m": winds,
        "weather_code": codes,
    }


class RequestParamsTests(unittest.TestCase):
    def test_wind_requested_in_meters_per_second(self):
        """Регресс: без windspeed_unit API отдаёт км/ч, а код подписывал их как м/с."""
        params = wb.build_request_params(55.42, 37.54, days=2)
        self.assertEqual(params["wind_speed_unit"], "ms")

    def test_uses_current_field_names(self):
        params = wb.build_request_params(55.42, 37.54, days=2)
        self.assertIn("wind_speed_10m", params["hourly"])
        self.assertIn("weather_code", params["hourly"])
        self.assertNotIn("windspeed_10m", params["hourly"])
        self.assertNotIn("weathercode", params["hourly"])

    def test_requested_timezone_is_moscow(self):
        self.assertEqual(wb.build_request_params(1, 2, days=1)["timezone"], "Europe/Moscow")

    def test_two_days_requested_for_tomorrow_section(self):
        self.assertEqual(wb.build_request_params(1, 2, days=2)["forecast_days"], 2)


class ParseHourlyTests(unittest.TestCase):
    def test_parses_timestamps_as_aware_datetimes(self):
        points = wb.parse_hourly(make_hours())
        self.assertEqual(points[0].time.hour, 0)
        self.assertIsNotNone(points[0].time.tzinfo)

    def test_skips_null_temperatures(self):
        hours = make_hours()
        hours["temperature_2m"][0] = None
        points = wb.parse_hourly(hours)
        self.assertEqual(len(points), 23)
        self.assertEqual(points[0].time.hour, 1)

    def test_points_sorted_by_time(self):
        hours = make_hours(date="2026-01-31")
        tomorrow = make_hours(date="2026-02-01")
        combined = {k: hours[k] + tomorrow[k] for k in hours}
        combined["time"] = tomorrow["time"] + hours["time"]
        points = wb.parse_hourly(combined)
        self.assertEqual(points, sorted(points, key=lambda p: p.time))

    def test_empty_payload_gives_empty_list(self):
        self.assertEqual(wb.parse_hourly({}), [])


class RepresentativeCodeTests(unittest.TestCase):
    def test_drizzle_beats_overcast(self):
        self.assertEqual(wb.pick_representative([3, 3, 3, 51]), 51)

    def test_fog_beats_clear_and_partly(self):
        self.assertEqual(wb.pick_representative([0, 2, 45, 2]), 45)

    def test_overcast_beats_partly_and_clear(self):
        self.assertEqual(wb.pick_representative([0, 1, 3, 2]), 3)

    def test_rain_beats_drizzle_by_frequency_within_tier(self):
        self.assertEqual(wb.pick_representative([51, 63, 63]), 63)

    def test_thunder_has_highest_priority(self):
        self.assertEqual(wb.pick_representative([65, 65, 65, 95]), 95)

    def test_snow_outranks_rain(self):
        self.assertEqual(wb.pick_representative([61, 71, 71, 3]), 71)

    def test_snow_severity_within_tier(self):
        self.assertEqual(wb.pick_representative([71, 75]), 75)

    def test_empty_gives_unknown(self):
        self.assertIsNone(wb.pick_representative([]))


class AggregateTests(unittest.TestCase):
    def test_min_max_and_wind_are_per_period(self):
        hours = make_hours(
            temps=[0] * 6 + [-5, -4, -3, -2] + [0] * 14,
            winds=[1] * 6 + [2, 4, 6, 8] + [1] * 14,
        )
        points = wb.parse_hourly(hours)
        periods = wb.periods_for_day(points, day_offset=0, now=NOW)
        first = periods["06:00 – 10:00"]
        self.assertEqual(first["min_temp"], -5)
        self.assertEqual(first["max_temp"], -2)
        self.assertEqual(first["wind"], 8.0)

    def test_wind_is_not_multiplied_or_divided(self):
        """API возвращает м/с; значение должно доходить до вывода как есть."""
        hours = make_hours(winds=[4.4] * 24)
        periods = wb.periods_for_day(wb.parse_hourly(hours), day_offset=0, now=NOW)
        self.assertAlmostEqual(periods["06:00 – 10:00"]["wind"], 4.4, places=1)

    def test_precipitation_is_summed_and_pop_is_max(self):
        pops = [0] * 6 + [80, 70, 60, 10] + [0] * 14
        precips = [0.0] * 6 + [0.5, 0.4, 0.2, 0.1] + [0.0] * 14
        periods = wb.periods_for_day(
            wb.parse_hourly(make_hours(pops=pops, precips=precips)), day_offset=0, now=NOW
        )
        period = periods["06:00 – 10:00"]
        self.assertEqual(period["pop"], 80)
        self.assertAlmostEqual(period["precip"], 1.2, places=1)

    def test_period_without_data_is_skipped(self):
        points = wb.parse_hourly(make_hours())[:8]  # только 00:00–07:00
        periods = wb.periods_for_day(points, day_offset=0, now=NOW)
        self.assertIn("06:00 – 10:00", periods)
        self.assertNotIn("14:00 – 18:00", periods)

    def test_tomorrow_uses_correct_day(self):
        today = wb.parse_hourly(make_hours(date="2026-01-31", temps=[0] * 24))
        tomorrow = wb.parse_hourly(make_hours(date="2026-02-01", temps=[10] * 24))
        periods = wb.periods_for_day(today + tomorrow, day_offset=1, now=NOW)
        self.assertEqual(periods["06:00 – 10:00"]["min_temp"], 10)

    def test_empty_period_data_returns_empty(self):
        self.assertEqual(wb.periods_for_day([], day_offset=0, now=NOW), {})


class TomorrowSummaryTests(unittest.TestCase):
    def test_summarizes_temp_range_and_rain(self):
        tomorrow = wb.parse_hourly(
            make_hours(date="2026-02-01", temps=[-10] * 12 + [0] * 12,
                       codes=[71] * 24, pops=[60] * 24)
        )
        summary = wb.summarize_day(tomorrow, day_offset=1, now=NOW)
        self.assertEqual(summary["min_temp"], -10)
        self.assertEqual(summary["max_temp"], 0)
        self.assertEqual(summary["code"], 71)
        self.assertEqual(summary["pop"], 60)

    def test_none_when_no_data(self):
        self.assertIsNone(wb.summarize_day([], day_offset=1, now=NOW))


class AdviceTests(unittest.TestCase):
    def advice_text(self, periods, tomorrow=None):
        return "\n".join(wb.generate_advice("Подольск", periods, tomorrow))

    def periods_with(self, **overrides):
        base = {
            "min_temp": 5, "max_temp": 7, "min_feels": 5, "max_feels": 7,
            "pop": 0, "precip": 0.0, "wind": 3.0, "code": 3, "desc": "Пасмурно", "icon": "☁️",
        }
        base.update(overrides)
        return {"06:00 – 10:00": dict(base)}

    def test_frost_advice(self):
        self.assertIn("пуховик", self.advice_text(self.periods_with(min_feels=-20, max_feels=-18)))

    def test_near_zero_advice(self):
        self.assertIn("демисезонная", self.advice_text(self.periods_with(min_feels=1, max_feels=3)))

    def test_hot_advice(self):
        self.assertIn("Жарко", self.advice_text(self.periods_with(min_feels=25, max_feels=28)))

    def test_rain_adds_umbrella_with_city_and_period(self):
        text = self.advice_text(self.periods_with(code=61))
        self.assertIn("зонт", text.lower())
        self.assertIn("Подольск", text)

    def test_high_pop_without_rain_code_still_adds_umbrella(self):
        self.assertIn("зонт", self.advice_text(self.periods_with(pop=60, code=3)).lower())

    def test_light_pop_without_rain_does_not_add_umbrella(self):
        self.assertIn(
            "не нужен", self.advice_text(self.periods_with(pop=20, precip=0.0, code=3))
        )

    def test_snow_without_rain_mentions_footwear(self):
        text = self.advice_text(self.periods_with(code=71, pop=0, precip=0.0))
        self.assertIn("обувь", text)

    def test_large_swing_adds_layering(self):
        text = self.advice_text(self.periods_with(min_feels=-2, max_feels=12))
        self.assertIn("многослойно", text)

    def test_small_swing_has_no_layering_note(self):
        self.assertNotIn("многослойно", self.advice_text(self.periods_with(min_feels=5, max_feels=8)))

    def test_strong_wind_line_uses_ms_threshold(self):
        """Регресс: порог 10 м/с. При км/ч сюда попадал почти каждый день."""
        self.assertNotIn("Ветер", self.advice_text(self.periods_with(wind=4.4)))
        self.assertIn("м/с", self.advice_text(self.periods_with(wind=12.0)))

    def test_advice_is_built_per_city(self):
        dry = self.periods_with(code=0, pop=0)
        rainy = self.periods_with(code=61, pop=90)
        self.assertIn("зонт", "\n".join(wb.generate_advice("Москва", rainy)).lower())
        self.assertIn("не нужен", "\n".join(wb.generate_advice("Москва", dry)).lower())

    def test_tomorrow_is_included_in_advice(self):
        tomorrow = {
            "min_temp": -20, "max_temp": -18, "min_feels": -25, "max_feels": -22,
            "pop": 0, "precip": 0.0, "wind": 2.0, "code": 0, "desc": "Ясно", "icon": "☀️",
        }
        text = self.advice_text(self.periods_with(min_feels=5, max_feels=8), tomorrow)
        self.assertIn("пуховик", text)


class MessageTests(unittest.TestCase):
    def forecasts(self):
        periods = {
            "06:00 – 10:00": {
                "min_temp": -3, "max_temp": -1, "min_feels": -6, "max_feels": -3,
                "pop": 40, "precip": 0.3, "wind": 5.0, "code": 71,
                "desc": "Небольшой снегопад", "icon": "❄️",
            }
        }
        return {
            "Podolsk": {
                "name": "Подольск", "icon": "🏠",

                "today": periods,
                "tomorrow": {
                    "min_temp": -10, "max_temp": -4, "min_feels": -14, "max_feels": -7,
                    "pop": 60, "precip": 0.9, "wind": 6.0, "code": 73,
                    "desc": "Снегопад", "icon": "❄️",
                },
                "current": {
                    "temp": -2, "feels": -5, "wind": 5.0,
                    "desc": "Небольшой снегопад", "icon": "❄️",
                },
            }
        }

    def test_message_contains_city_period_and_advice(self):
        text = wb.build_message(self.forecasts(), NOW)
        self.assertIn("Подольск", text)
        self.assertIn("06:00 – 10:00", text)
        self.assertIn("Что надеть", text)

    def test_message_shows_tomorrow_summary(self):
        self.assertIn("Завтра", wb.build_message(self.forecasts(), NOW))

    def test_message_shows_current_hour(self):
        self.assertIn("Сейчас", wb.build_message(self.forecasts(), NOW))

    def test_message_without_current_block_still_renders(self):
        forecasts = self.forecasts()
        forecasts["Podolsk"]["current"] = None
        self.assertNotIn("Сейчас", wb.build_message(forecasts, NOW))

    def test_message_without_tomorrow_still_renders(self):
        forecasts = self.forecasts()
        forecasts["Podolsk"]["tomorrow"] = None
        self.assertNotIn("Завтра", wb.build_message(forecasts, NOW))

    def test_temperature_formatting(self):
        text = wb.build_message(self.forecasts(), NOW)
        self.assertIn("-3…-1°C", text)

    def test_update_time_is_rendered(self):
        self.assertIn("03:53", wb.build_message(self.forecasts(), NOW))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.sent = []
        self.hours = make_hours(temps=[1] * 24, codes=[3] * 24)
        self.tomorrow = make_hours(date="2026-02-01", temps=[2] * 24, codes=[3] * 24)
        self.payload = {
            k: self.hours[k] + self.tomorrow[k]
            for k in ("time", "temperature_2m", "apparent_temperature",
                      "precipitation_probability", "precipitation",
                      "wind_speed_10m", "weather_code")
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_bot(self, *, sender=None, **kwargs):
        """Вызов run с изолированным состоянием: тесты не трогают data/state.json."""
        kwargs.setdefault("state", storage.empty_state())
        kwargs.setdefault("state_path", self.state_path)
        kwargs.setdefault("send", sender or self.send)
        return wb.run(now=NOW, **kwargs)

    def fake_get(self, url, params=None, timeout=None):
        self.last_params = params
        return {"hourly": self.payload}

    def send(self, text):
        self.sent.append(text)
        return True

    def test_sends_single_message_with_both_cities(self):
        ok = self.run_bot(http_get=self.fake_get)
        self.assertTrue(ok)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Подольск", self.sent[0])
        self.assertIn("Москва", self.sent[0])

    def test_request_uses_ms_wind_unit(self):
        self.run_bot(http_get=self.fake_get)
        self.assertEqual(self.last_params["wind_speed_unit"], "ms")

    def test_one_city_failure_does_not_kill_forecast(self):
        calls = {"n": 0}

        def flaky_get(url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("timeout")
            return self.fake_get(url, params=params, timeout=timeout)

        ok = self.run_bot(http_get=flaky_get)
        self.assertTrue(ok)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Москва", self.sent[0])

    def test_partial_failure_is_reported_in_message(self):
        calls = {"n": 0}

        def flaky_get(url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("timeout")
            return self.fake_get(url, params=params, timeout=timeout)

        self.run_bot(http_get=flaky_get)
        self.assertIn("Не удалось получить прогноз", self.sent[0])

    def test_total_failure_sends_alert_and_returns_false(self):
        def dead_get(url, params=None, timeout=None):
            raise OSError("dns fail")

        alerts = []
        ok = self.run_bot(
            http_get=dead_get,
            send_alert=lambda text: alerts.append(text) or True,
        )
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(alerts), 1)

    def test_alert_not_repeated_within_cooldown(self):
        def dead_get(url, params=None, timeout=None):
            raise OSError("dns fail")

        alerts = []
        state = storage.empty_state()
        shared = dict(
            http_get=dead_get,
            send_alert=lambda text: alerts.append(text) or True,
            state=state,
            state_path=self.state_path,
        )
        wb.run(now=NOW, **shared)
        wb.run(now=NOW + timedelta(minutes=30), **shared)
        self.assertEqual(len(alerts), 1)

    def test_alert_repeats_after_cooldown(self):
        def dead_get(url, params=None, timeout=None):
            raise OSError("dns fail")

        alerts = []
        state = storage.empty_state()
        shared = dict(
            http_get=dead_get,
            send_alert=lambda text: alerts.append(text) or True,
            state=state,
            state_path=self.state_path,
        )
        wb.run(now=NOW, **shared)
        wb.run(now=NOW + timedelta(hours=wb.ALERT_COOLDOWN_HOURS + 1), **shared)
        self.assertEqual(len(alerts), 2)

    def test_delivery_failure_returns_false(self):
        ok = self.run_bot(http_get=self.fake_get, sender=lambda _text: False)
        self.assertFalse(ok)

    def test_check_mode_prints_and_does_not_send(self):
        ok = self.run_bot(http_get=self.fake_get, check_mode=True)
        self.assertTrue(ok)
        self.assertEqual(self.sent, [])

    def test_sender_exception_is_handled(self):
        def broken_sender(_text):
            raise RuntimeError("telegram down")

        ok = self.run_bot(http_get=self.fake_get, sender=broken_sender)
        self.assertFalse(ok)

    def test_empty_api_response_is_treated_as_failure(self):
        ok = self.run_bot(http_get=lambda *a, **k: {"hourly": {}})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
