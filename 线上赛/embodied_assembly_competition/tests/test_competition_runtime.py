from __future__ import annotations

import unittest
from datetime import datetime
from threading import Event

from assembly.competition_runtime import (
    CompetitionSession,
    RuntimeTaskInput,
    RuntimeVisionAgent,
)
from assembly.competition_logs import CompetitionEvidenceStore
from assembly.models import Color, EntityKind, SceneResult, TaskPlan
from assembly.task_plan import parse_assembly_instruction
from assembly.visionmaster_tcp import ReceivedTaskImage
from assembly.vm_protocol import fixed_vm_packets, parse_vm_measurement


INSTRUCTION = (
    "先把红色方块放到黄色托盘上，再把绿色方块放到红色托盘上，"
    "接着把橙色方块放到蓝色托盘上，然后把蓝色方块放到紫色托盘上，"
    "再把黄色方块放到橙色托盘上，最后把紫色方块放到绿色托盘上"
)


class FakeCardCollector:
    def __init__(self) -> None:
        self.images = {
            1: ReceivedTaskImage(b"BM-one", datetime.now(), "127.0.0.1", 7930),
            2: ReceivedTaskImage(b"BM-two", datetime.now(), "127.0.0.1", 7930),
        }
        self.requests: list[int] = []
        self.vm_requests: list[tuple[str, str]] = []
        self.measurements = {
            measurement.key: measurement
            for measurement in map(parse_vm_measurement, fixed_vm_packets())
        }

    def collect_card(self, card_number: int) -> ReceivedTaskImage:
        self.requests.append(card_number)
        return self.images[card_number]

    def collect_measurement(self, prefix: str, trigger: str):
        self.vm_requests.append((prefix, trigger))
        if prefix == "wukuai":
            return self.measurements[
                (next(color for color in Color if color.block_trigger == trigger), EntityKind.BLOCK)
            ]
        if prefix == "tuopan":
            return self.measurements[
                (next(color for color in Color if color.tray_trigger == trigger), EntityKind.TRAY)
            ]
        raise AssertionError(f"unexpected VM prefix: {prefix}")


class FakeVisionModel:
    def __init__(self) -> None:
        self.task1_summary: str | None = None

    def analyze_task1(self, image) -> SceneResult:
        return SceneResult("识别到雨伞和齿轮。", "task1-trace")

    def analyze_task2(self, image, *, task1_summary: str) -> TaskPlan:
        self.task1_summary = task1_summary
        return TaskPlan(
            task1_summary=task1_summary,
            task2_instruction=INSTRUCTION,
            steps=parse_assembly_instruction(INSTRUCTION),
            model_trace="task2-trace",
        )


class FakeWorkflow:
    def __init__(self) -> None:
        self.started = False

    def run(self) -> None:
        self.started = True


class CompetitionRuntimeTests(unittest.TestCase):
    def test_session_starts_workflow_without_text_input(self) -> None:
        workflow = FakeWorkflow()
        evidence = CompetitionEvidenceStore("test_run", datetime.now().astimezone())
        traces: list[str] = []
        CompetitionSession(
            workflow,
            Event(),
            lambda: 0,
            lambda message: traces.append(message) or 0,
            lambda: (),
            evidence,
            None,
        ).start()

        self.assertTrue(workflow.started)
        self.assertEqual(
            traces,
            [
                "COMPETITION_START run_id=test_run",
                "COMPETITION_END run_id=test_run status=COMPLETED",
            ],
        )
        self.assertEqual(evidence.snapshot().status, "已完成")

    def test_session_blocks_start_after_timeout_and_requests_stop(self) -> None:
        workflow = FakeWorkflow()
        stopped: list[bool] = []
        evidence = CompetitionEvidenceStore("test_run", datetime.now().astimezone())
        session = CompetitionSession(
            workflow,
            Event(),
            lambda: stopped.append(True) or 0,
            lambda message: 0,
            lambda: (),
            evidence,
            None,
        )

        session.stop_for_timeout()

        self.assertEqual(stopped, [True])
        with self.assertRaisesRegex(RuntimeError, "计时已结束"):
            session.start()

    def test_requests_task_card_2_only_after_task_card_1_analysis(self) -> None:
        cards = FakeCardCollector()
        reports: list[str] = []
        evidence = CompetitionEvidenceStore("test_run", datetime.now().astimezone())
        task_input = RuntimeTaskInput(cards, reports.append, evidence)
        model = FakeVisionModel()
        agent = RuntimeVisionAgent(task_input, model, evidence)

        task1 = task_input.get_card(1)
        scene = agent.summarize_scene(task1)
        self.assertEqual(cards.requests, [1])

        task2 = task_input.get_card(2)
        plan = agent.build_assembly_plan(task2)

        self.assertEqual(cards.requests, [1, 2])
        self.assertEqual(scene.summary, "识别到雨伞和齿轮。")
        self.assertEqual(model.task1_summary, scene.summary)
        self.assertEqual(len(plan.steps), 6)
        self.assertTrue(any("任务卡 1 已接收" in report for report in reports))
        self.assertTrue(any("任务卡 2 已接收" in report for report in reports))

    def test_requests_vm_block_then_tray_in_agent_step_order(self) -> None:
        cards = FakeCardCollector()
        reports: list[str] = []
        evidence = CompetitionEvidenceStore("test_run", datetime.now().astimezone())
        task_input = RuntimeTaskInput(cards, reports.append, evidence)

        received = task_input.get_measurements(parse_assembly_instruction(INSTRUCTION))

        self.assertEqual(cards.requests, [])
        self.assertEqual(len(received), 12)
        self.assertEqual(
            cards.vm_requests,
            [
                ("wukuai", "11"),
                ("wukuai", "14"),
                ("wukuai", "12"),
                ("wukuai", "15"),
                ("wukuai", "13"),
                ("wukuai", "16"),
                ("tuopan", "23"),
                ("tuopan", "21"),
                ("tuopan", "25"),
                ("tuopan", "26"),
                ("tuopan", "22"),
                ("tuopan", "24"),
            ],
        )
        self.assertTrue(
            any("wukuai,11" in report for report in reports)
        )
        self.assertTrue(
            any("tuopan,23" in report for report in reports)
        )
