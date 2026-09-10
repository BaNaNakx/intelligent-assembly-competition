from __future__ import annotations

import unittest
from datetime import datetime

from assembly.models import AssemblyStep, Color, EntityKind, TaskPlan
from assembly.task_plan import (
    TaskPlanValidationError,
    parse_assembly_instruction,
    validate_task_plan,
)
from assembly.vm_protocol import VisionMeasurementStore, fixed_vm_packets


SAMPLE_INSTRUCTION = (
    "先把红色方块放到黄色托盘上，再把绿色方块放到红色托盘上，"
    "接着把橙色方块放到蓝色托盘上，然后把蓝色方块放到紫色托盘上，"
    "再把黄色方块放到橙色托盘上，最后把紫色方块放到绿色托盘上。"
)


def _all_measurements():
    store = VisionMeasurementStore()
    for packet in fixed_vm_packets():
        store.ingest(packet)
    return store.snapshot()


def _valid_plan() -> TaskPlan:
    return TaskPlan(
        task1_summary="识别到笔记本电脑、齿轮、雨伞、螺丝刀、螺母和水杯。",
        task2_instruction=SAMPLE_INSTRUCTION,
        steps=parse_assembly_instruction(SAMPLE_INSTRUCTION),
        model_trace="测试用的结构化任务结论。",
        created_at=datetime(2026, 8, 1),
    )


class TaskPlanTests(unittest.TestCase):
    def test_parses_project_book_example_in_original_order(self) -> None:
        steps = parse_assembly_instruction(SAMPLE_INSTRUCTION)
        self.assertEqual(
            tuple(step.agent_trigger for step in steps),
            ("11 23", "14 21", "12 25", "15 26", "13 22", "16 24"),
        )

    def test_rejects_text_without_assembly_clause(self) -> None:
        with self.assertRaises(TaskPlanValidationError):
            parse_assembly_instruction("请完成本次装配任务。")

    def test_accepts_a_complete_six_step_plan(self) -> None:
        validated = validate_task_plan(_valid_plan(), _all_measurements())
        self.assertEqual(len(validated.steps), 6)

    def test_rejects_wrong_trigger_code(self) -> None:
        original = _valid_plan()
        invalid_step = AssemblyStep(
            index=1,
            block_color=Color.RED,
            tray_color=Color.YELLOW,
            block_trigger="12",
            tray_trigger="23",
        )
        invalid = TaskPlan(
            task1_summary=original.task1_summary,
            task2_instruction=original.task2_instruction,
            steps=(invalid_step,) + original.steps[1:],
            model_trace=original.model_trace,
        )
        with self.assertRaises(TaskPlanValidationError):
            validate_task_plan(invalid, _all_measurements())

    def test_rejects_duplicate_block_or_tray(self) -> None:
        original = _valid_plan()
        duplicate = AssemblyStep.from_colors(2, Color.RED, Color.RED)
        invalid = TaskPlan(
            task1_summary=original.task1_summary,
            task2_instruction=original.task2_instruction,
            steps=(original.steps[0], duplicate) + original.steps[2:],
            model_trace=original.model_trace,
        )
        with self.assertRaises(TaskPlanValidationError):
            validate_task_plan(invalid, _all_measurements())

    def test_rejects_missing_vm_measurement(self) -> None:
        measurements = _all_measurements()
        measurements.pop((Color.PURPLE, EntityKind.TRAY))
        with self.assertRaises(TaskPlanValidationError):
            validate_task_plan(_valid_plan(), measurements)
