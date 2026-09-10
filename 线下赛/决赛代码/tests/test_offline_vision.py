from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from assembly.competition_logs import CompetitionEvidenceStore
from assembly.models import AssemblyStep, Color, EntityKind, VisionMeasurement
from assembly.offline_vision import JustInTimeVisionLocator, OfflineVisionError


class FakeCollector:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.requests: list[str] = []
        self.offset = 0.0
        self.wrong_key = False
        self.stale = False

    def collect_measurement(self, trigger: str) -> VisionMeasurement:
        self.requests.append(trigger)
        color = next(
            color
            for color in Color
            if trigger in {color.block_trigger, color.tray_trigger}
        )
        kind = EntityKind.BLOCK if trigger.startswith("1") else EntityKind.TRAY
        if self.wrong_key:
            color = Color.PURPLE
        received_at = self.now - timedelta(seconds=1) if self.stale else self.now
        return VisionMeasurement(
            color,
            kind,
            100.0 + self.offset,
            200.0 + self.offset,
            0.0,
            f"#{100 + self.offset};{200 + self.offset};0",
            received_at,
        )


class OfflineVisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now().astimezone()
        self.collector = FakeCollector(self.now)
        self.evidence = CompetitionEvidenceStore("run", self.now)
        self.locator = JustInTimeVisionLocator(
            self.collector,
            lambda text: None,
            self.evidence,
            clock=lambda: self.now,
        )
        self.step = AssemblyStep.from_colors(1, Color.RED, Color.BLUE)

    def test_reads_current_block_then_tray_on_every_step_attempt(self) -> None:
        first = self.locator.locate_step(self.step)
        self.collector.offset = 35.0
        second = self.locator.locate_step(self.step)

        self.assertEqual(
            self.collector.requests,
            ["11", "25"] * 2,
        )
        self.assertEqual(first[(Color.RED, EntityKind.BLOCK)].x, 100.0)
        self.assertEqual(second[(Color.RED, EntityKind.BLOCK)].x, 135.0)
        self.assertEqual(len(self.evidence.snapshot().vm_records), 4)

    def test_rejects_wrong_color_or_target_before_motion(self) -> None:
        self.collector.wrong_key = True

        with self.assertRaisesRegex(OfflineVisionError, "错误的颜色或目标类别"):
            self.locator.locate_step(self.step)

    def test_rejects_measurement_created_before_request(self) -> None:
        self.collector.stale = True

        with self.assertRaisesRegex(OfflineVisionError, "旧坐标"):
            self.locator.locate_step(self.step)


if __name__ == "__main__":
    unittest.main()
