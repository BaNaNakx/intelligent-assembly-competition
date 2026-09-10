from __future__ import annotations

import unittest

from assembly.models import AssemblyStep, Color, EntityKind, TaskCard, TaskState


class ColorModelTests(unittest.TestCase):
    def test_project_book_color_trigger_mapping(self) -> None:
        self.assertEqual(Color.RED.block_trigger, "11")
        self.assertEqual(Color.ORANGE.block_trigger, "12")
        self.assertEqual(Color.YELLOW.tray_trigger, "23")
        self.assertEqual(Color.PURPLE.tray_trigger, "26")
        self.assertEqual(Color.BLUE.display_name, "蓝色")

    def test_step_builds_required_agent_trigger(self) -> None:
        step = AssemblyStep.from_colors(1, Color.RED, Color.YELLOW)
        self.assertEqual(step.agent_trigger, "11 23")
        self.assertEqual(step.block_color, Color.RED)
        self.assertEqual(step.tray_color, Color.YELLOW)

    def test_task_card_rejects_wrong_card_content(self) -> None:
        with self.assertRaises(ValueError):
            TaskCard(number=1)
        with self.assertRaises(ValueError):
            TaskCard(number=2)
        with self.assertRaises(ValueError):
            TaskCard(number=3, scene_objects=("杯子",))

    def test_core_enums_are_stable(self) -> None:
        self.assertEqual(EntityKind.BLOCK.value, "block")
        self.assertEqual(TaskState.COMPLETED.value, "completed")
