'Automatic VisionMaster-to-Qwen stage for the two task cards.'

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import SceneResult, TaskPlan
from .visionmaster_tcp import ReceivedTaskCards, ReceivedTaskImage


class TaskCardImageSourcePort(Protocol):
    def collect_card(self, card_number: int) -> ReceivedTaskImage: ...


class TaskCardVisionAgentPort(Protocol):
    def analyze_task1(self, image: ReceivedTaskImage) -> SceneResult: ...

    def analyze_task2(
        self, image: ReceivedTaskImage, *, task1_summary: str
    ) -> TaskPlan: ...


@dataclass(frozen=True, slots=True)
class VisionStageResult:
    cards: ReceivedTaskCards
    scene: SceneResult
    plan: TaskPlan


class AutomatedVisionStage:

    'Request and analyze task cards in the required competition order.'
    def __init__(
        self, image_source: TaskCardImageSourcePort, agent: TaskCardVisionAgentPort
    ) -> None:
        self._image_source = image_source
        self._agent = agent

    def run(self) -> VisionStageResult:
        card1 = self._image_source.collect_card(1)
        scene = self._agent.analyze_task1(card1)
        card2 = self._image_source.collect_card(2)
        plan = self._agent.analyze_task2(
            card2, task1_summary=scene.summary
        )
        return VisionStageResult(
            cards=ReceivedTaskCards(card1=card1, card2=card2),
            scene=scene,
            plan=plan,
        )
