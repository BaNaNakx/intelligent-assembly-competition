from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from assembly.audit import AuditLogger
from assembly.models import Color, EntityKind, SceneResult, TaskCard, TaskPlan
from assembly.task_plan import parse_assembly_instruction
from assembly.vm_protocol import VisionMeasurementStore, fixed_vm_packets
from assembly.workflow import (
    ALL_TASKS_COMPLETED_RESPONSE,
    TASK1_COMPLETED_RESPONSE,
    CompetitionWorkflow,
)


INSTRUCTION = (
    "先把红色方块放到黄色托盘上，再把绿色方块放到红色托盘上，"
    "接着把橙色方块放到蓝色托盘上，然后把蓝色方块放到紫色托盘上，"
    "再把黄色方块放到橙色托盘上，最后把紫色方块放到绿色托盘上。"
)


class FakeTaskInput:
    def __init__(self) -> None:
        self._cards = {
            1: TaskCard(number=1, scene_objects=("笔记本电脑", "齿轮", "雨伞")),
            2: TaskCard(number=2, instruction_text=INSTRUCTION),
        }
        store = VisionMeasurementStore()
        for packet in fixed_vm_packets():
            store.ingest(packet)
        self._measurements = store.snapshot()

    def get_card(self, number: int) -> TaskCard:
        return self._cards[number]

    def get_measurements(self, steps):
        return self._measurements


class FakeAgent:
    def summarize_scene(self, card: TaskCard) -> SceneResult:
        return SceneResult(
            summary="识别到笔记本电脑、齿轮和雨伞。",
            model_trace=f"根据任务卡 {card.number} 的场景对象生成摘要。",
        )

    def build_assembly_plan(self, card: TaskCard) -> TaskPlan:
        return TaskPlan(
            task1_summary="识别到笔记本电脑、齿轮和雨伞。",
            task2_instruction=card.instruction_text or "",
            steps=parse_assembly_instruction(card.instruction_text or ""),
            model_trace="根据任务卡 2 的原始指令生成六步计划。",
            created_at=datetime(2026, 8, 1),
        )


class FakeSpeaker:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def speak(self, text: str) -> None:
        self.messages.append(text)


class FakeExecutor:
    def __init__(self) -> None:
        self.triggers: list[str] = []

    def execute_step(self, step, measurements) -> None:
        self.triggers.append(step.agent_trigger)
        self.measurement_count = len(measurements)


class CompetitionWorkflowTests(unittest.TestCase):
    def test_required_procedure_runs_directly(self) -> None:
        speaker = FakeSpeaker()
        executor = FakeExecutor()
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = AuditLogger(Path(temp_dir), "workflow")
            workflow = CompetitionWorkflow(
                task_input=FakeTaskInput(),
                agent=FakeAgent(),
                speaker=speaker,
                executor=executor,
                logger=logger,
            )

            workflow.run()

            self.assertEqual(
                speaker.messages,
                [
                    "识别到笔记本电脑、齿轮和雨伞。",
                    TASK1_COMPLETED_RESPONSE,
                    INSTRUCTION,
                    ALL_TASKS_COMPLETED_RESPONSE,
                ],
            )
            self.assertEqual(
                executor.triggers,
                ["11 23", "14 21", "12 25", "15 26", "13 22", "16 24"],
            )
            self.assertEqual(executor.measurement_count, 12)
            self.assertEqual(workflow.state.value, "completed")

            log_text = logger.text_path.read_text(encoding="utf-8")
            self.assertIn("model_scene_result", log_text)
            self.assertIn("model_assembly_plan", log_text)
            self.assertIn("11 23", log_text)
