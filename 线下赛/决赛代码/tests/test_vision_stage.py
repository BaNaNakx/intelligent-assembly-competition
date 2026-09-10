from __future__ import annotations

import unittest
from datetime import datetime

from assembly.models import AssemblyStep, Color, SceneResult, TaskPlan
from assembly.vision_stage import AutomatedVisionStage
from assembly.visionmaster_tcp import ReceivedTaskCards, ReceivedTaskImage


def _image(label: bytes) -> ReceivedTaskImage:
    return ReceivedTaskImage(
        data=b"\x89PNG\r\n\x1a\n" + label,
        received_at=datetime.now().astimezone(),
        host="127.0.0.1",
        port=7930,
    )


class FakeImageSource:
    def __init__(self) -> None:
        self.cards = ReceivedTaskCards(card1=_image(b"one"), card2=_image(b"two"))
        self.requests: list[int] = []

    def collect_card(self, card_number: int) -> ReceivedTaskImage:
        self.requests.append(card_number)
        return self.cards.card1 if card_number == 1 else self.cards.card2


class FakeVisionAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze_task1(self, image: ReceivedTaskImage) -> SceneResult:
        self.calls.append(f"task1:{image.data[-3:].decode()}")
        return SceneResult(summary="识别完成。", model_trace="{}")

    def analyze_task2(
        self, image: ReceivedTaskImage, *, task1_summary: str
    ) -> TaskPlan:
        self.calls.append(f"task2:{image.data[-3:].decode()}:{task1_summary}")
        return TaskPlan(
            task1_summary=task1_summary,
            task2_instruction="测试指令",
            steps=(AssemblyStep.from_colors(1, Color.RED, Color.YELLOW),),
            model_trace="{}",
        )


class AutomatedVisionStageTests(unittest.TestCase):
    def test_collects_then_analyzes_cards_in_required_order(self) -> None:
        agent = FakeVisionAgent()
        source = FakeImageSource()
        result = AutomatedVisionStage(source, agent).run()

        self.assertEqual(agent.calls, ["task1:one", "task2:two:识别完成。"])
        self.assertEqual(source.requests, [1, 2])
        self.assertEqual(result.plan.task1_summary, "识别完成。")
