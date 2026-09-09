"""Tests for deterministic sidecar scheduling behavior."""

from __future__ import annotations

import unittest
from datetime import datetime

from fitbit.scheduler import parse_schedule, seconds_until_next_run


class SchedulerTest(unittest.TestCase):
    def test_default_style_schedule_is_sorted(self) -> None:
        self.assertEqual(parse_schedule("20:00,08:00"), ((8, 0), (20, 0)))

    def test_next_run_uses_same_day_then_wraps(self) -> None:
        schedule = ((8, 0), (20, 0))
        morning = datetime(2026, 9, 7, 7, 30)
        evening = datetime(2026, 9, 7, 20, 30)
        self.assertEqual(seconds_until_next_run(morning, schedule), 1_800)
        self.assertEqual(seconds_until_next_run(evening, schedule), 41_400)

    def test_invalid_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_schedule("25:00")


if __name__ == "__main__":
    unittest.main()
