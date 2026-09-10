from __future__ import annotations

import unittest

from assembly.competition_clock import CompetitionClock


class CompetitionClockTests(unittest.TestCase):
    def test_elapsed_and_remaining_share_one_start_time(self) -> None:
        clock = CompetitionClock(started_at=100.0, duration_seconds=300)

        self.assertEqual(clock.elapsed_seconds(163.9), 63)
        self.assertEqual(clock.remaining_seconds(163.9), 237)
        self.assertEqual(clock.remaining_seconds(500.0), 0)

    def test_pause_freezes_both_clocks_and_resume_keeps_elapsed_time(self) -> None:
        clock = CompetitionClock(started_at=100.0, duration_seconds=300)

        clock.pause(163.9)
        self.assertTrue(clock.is_paused)
        self.assertEqual(clock.elapsed_seconds(250.0), 63)
        self.assertEqual(clock.remaining_seconds(250.0), 237)

        clock.resume(203.9)
        self.assertFalse(clock.is_paused)
        self.assertEqual(clock.elapsed_seconds(213.9), 73)
        self.assertEqual(clock.remaining_seconds(213.9), 227)
