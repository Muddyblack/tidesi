"""The window length a quota row carries for the period comparison."""

import calendar
import datetime
import unittest

import _support  # noqa: F401 — puts the backend package on sys.path
from tidesi.contract import MONTH, flat_window, period_ms, quota_window

DAY = 86400000


def epoch(year, month, day):
    return calendar.timegm(datetime.datetime(year, month, day, 12).timetuple())


class RowPeriodTest(unittest.TestCase):
    def test_a_fixed_length_is_passed_through(self):
        self.assertEqual(period_ms(18000000, 0), 18000000)
        self.assertEqual(period_ms(604800000.0, 0), 604800000)

    def test_no_or_nonsense_length_is_no_period(self):
        for value in (0, -5, None, "week", True):
            with self.subTest(value=value):
                self.assertEqual(period_ms(value, epoch(2026, 3, 1)), 0)

    def test_a_month_is_the_calendar_month_that_ends_at_the_reset(self):
        self.assertEqual(period_ms(MONTH, epoch(2026, 3, 1)), 28 * DAY)  # February
        self.assertEqual(period_ms(MONTH, epoch(2024, 3, 1)), 29 * DAY)  # leap February
        self.assertEqual(period_ms(MONTH, epoch(2026, 2, 1)), 31 * DAY)  # January
        self.assertEqual(period_ms(MONTH, epoch(2026, 1, 15)), 31 * DAY)  # across the year
        self.assertEqual(period_ms(MONTH, epoch(2026, 5, 10)), 30 * DAY)  # April
        # The 31st has no counterpart in a shorter month: the window began on its last day.
        self.assertEqual(period_ms(MONTH, epoch(2026, 3, 31)), 31 * DAY)

    def test_a_month_without_a_reset_is_no_period(self):
        self.assertEqual(period_ms(MONTH, 0), 0)
        self.assertEqual(period_ms(MONTH, 10**18), 0)

    def test_rows_carry_the_period_only_when_there_is_one(self):
        reset = epoch(2026, 3, 1)
        self.assertEqual(flat_window("k", "K", 10, reset, "", True, history="x", period=MONTH)["periodMs"], 28 * DAY)
        self.assertNotIn("periodMs", flat_window("k", "K", 10, 0, "", True, history="x", period=MONTH))
        self.assertNotIn("periodMs", flat_window("k", "K", 10, reset, "", True, history="x"))
        row = quota_window("k", "K", {"available": True, "pct": 5, "resetAt": reset}, "", history="s", period=18000000)
        self.assertEqual((row["historyKey"], row["periodMs"]), ("s", 18000000))


if __name__ == "__main__":
    unittest.main()
