"""Граничные случаи погодного бота: сутки, таймзона, неполные данные API."""

import unittest
from datetime import datetime, timedelta, timezone

import weather_bot as wb

MSK = timezone(timedelta(hours=3))


def hours_for(date_str, temps=None, codes=None):
    temps = temps if temps is not None else [5] * 24
    codes = codes if codes is not None else [0] * 24
    return {
        "time": [f"{date_str}T{h:02d}:00" for h in range(24)],
        "temperature_2m": temps,
        "apparent_temperature": temps,
        "precipitation_probability": [0] * 24,
        "precipitation": [0.0] * 24,
        "wind_speed_10m": [2.0] * 24,
        "weather_code": codes,
    }


class DayBoundaryTests(unittest.TestCase):
    """Open-Meteo отдаёт время в Europe/Moscow; даты обязаны сравниваться в МСК.

    Если сравнивать в UTC, то в интервале 21:00–24:00 МСК «сегодня» и «завтра»
    съезжают на сутки: московская полночь — это ещё 21:00 предыдущего дня UTC.
    """

    def test_just_before_midnight_today_is_current_day(self):
        now = datetime(2026, 1, 31, 23, 30, tzinfo=MSK)
        points = wb.parse_hourly(hours_for("2026-01-31", temps=[-1] * 24)
                                 | {"time": [f"2026-01-31T{h:02d}:00" for h in range(24)]})
        periods = wb.periods_for_day(points, day_offset=0, now=now)
        self.assertIn("18:00 – 22:00", periods)

    def test_tomorrow_is_next_calendar_day(self):
        now = datetime(2026, 1, 31, 23, 30, tzinfo=MSK)
        today = wb.parse_hourly(hours_for("2026-01-31", temps=[0] * 24))
        tomorrow = wb.parse_hourly(hours_for("2026-02-01", temps=[10] * 24))
        summary = wb.summarize_day(today + tomorrow, day_offset=1, now=now)
        self.assertEqual(summary["min_temp"], 10)

    def test_after_midnight_yesterday_data_is_not_today(self):
        now = datetime(2026, 2, 1, 0, 30, tzinfo=MSK)
        yesterday = wb.parse_hourly(hours_for("2026-01-31", temps=[-20] * 24))
        today = wb.parse_hourly(hours_for("2026-02-01", temps=[3] * 24))
        periods = wb.periods_for_day(yesterday + today, day_offset=0, now=now)
        self.assertEqual(periods["06:00 – 10:00"]["min_temp"], 3)

    def test_month_boundary(self):
        now = datetime(2026, 1, 31, 20, 0, tzinfo=MSK)
        today = wb.parse_hourly(hours_for("2026-01-31", temps=[-5] * 24))
        tomorrow = wb.parse_hourly(hours_for("2026-02-01", temps=[7] * 24))
        self.assertEqual(
            wb.summarize_day(today + tomorrow, day_offset=1, now=now)["min_temp"], 7
        )

    def test_current_snapshot_picks_latest_past_hour(self):
        now = datetime(2026, 1, 31, 14, 20, tzinfo=MSK)
        points = wb.parse_hourly(hours_for("2026-01-31", temps=[float(h) for h in range(24)]))
        current = wb.current_snapshot(points, now)
        self.assertEqual(current["temp"], 14)

    def test_current_snapshot_at_midnight_uses_first_hour(self):
        now = datetime(2026, 1, 31, 0, 5, tzinfo=MSK)
        points = wb.parse_hourly(hours_for("2026-01-31", temps=[float(h) for h in range(24)]))
        self.assertEqual(wb.current_snapshot(points, now)["temp"], 0)

    def test_current_snapshot_without_today_data(self):
        now = datetime(2026, 2, 5, 12, 0, tzinfo=MSK)
        points = wb.parse_hourly(hours_for("2026-01-31"))
        self.assertIsNone(wb.current_snapshot(points, now))


class IncompleteDataTests(unittest.TestCase):
    def test_null_wind_treated_as_zero(self):
        hours = hours_for("2026-01-31")
        hours["wind_speed_10m"] = [None] * 24
        periods = wb.periods_for_day(
            wb.parse_hourly(hours), day_offset=0,
            now=datetime(2026, 1, 31, 12, 0, tzinfo=MSK),
        )
        self.assertEqual(periods["06:00 – 10:00"]["wind"], 0.0)

    def test_null_pop_does_not_crash(self):
        hours = hours_for("2026-01-31")
        hours["precipitation_probability"] = [None] * 24
        periods = wb.periods_for_day(
            wb.parse_hourly(hours), day_offset=0,
            now=datetime(2026, 1, 31, 12, 0, tzinfo=MSK),
        )
        self.assertEqual(periods["06:00 – 10:00"]["pop"], 0)

    def test_null_code_gives_placeholder(self):
        hours = hours_for("2026-01-31")
        hours["weather_code"] = [None] * 24
        periods = wb.periods_for_day(
            wb.parse_hourly(hours), day_offset=0,
            now=datetime(2026, 1, 31, 12, 0, tzinfo=MSK),
        )
        self.assertEqual(periods["06:00 – 10:00"]["desc"], "Нет данных")

    def test_shorter_than_expected_arrays(self):
        hours = hours_for("2026-01-31")
        for key in ("temperature_2m", "apparent_temperature", "precipitation_probability",
                    "precipitation", "wind_speed_10m", "weather_code"):
            hours[key] = hours[key][:8]
        points = wb.parse_hourly(hours)
        self.assertEqual(len(points), 8)
        periods = wb.periods_for_day(
            points, day_offset=0, now=datetime(2026, 1, 31, 12, 0, tzinfo=MSK)
        )
        self.assertIn("06:00 – 10:00", periods)

    def test_missing_time_key(self):
        self.assertEqual(wb.parse_hourly({"temperature_2m": [1, 2]}), [])

    def test_unknown_wmo_code(self):
        self.assertEqual(wb.decode_weather(1234)[0], "Неизвестно")

    def test_string_numbers_are_parsed(self):
        hours = hours_for("2026-01-31")
        hours["temperature_2m"] = ["5.4"] * 24
        points = wb.parse_hourly(hours)
        self.assertEqual(points[0].temp, 5.4)


if __name__ == "__main__":
    unittest.main()
