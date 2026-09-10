'Competition procedure orchestration with replaceable external ports.'

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from .audit import AuditLogger
from .models import (
    AssemblyStep,
    AuditEvent,
    Color,
    EntityKind,
    SceneResult,
    TaskCard,
    TaskPlan,
    TaskState,
    VisionMeasurement,
)
from .task_plan import validate_task_plan


TASK1_COMPLETED_RESPONSE = "任务已完成"
ALL_TASKS_COMPLETED_RESPONSE = "两个任务均已完成"


class TaskInputPort(Protocol):

    'Supplies VisionMaster task-card data and fixed workspace measurements.'
    def get_card(self, number: int) -> TaskCard: ...

    def get_measurements(
        self, steps: Sequence[AssemblyStep]
    ) -> Mapping[tuple[Color, EntityKind], VisionMeasurement]: ...


class IntelligentAgentPort(Protocol):

    'Produces displayable and structured task conclusions.'
    def summarize_scene(self, card: TaskCard) -> SceneResult: ...

    def build_assembly_plan(self, card: TaskCard) -> TaskPlan: ...


class SpeakerPort(Protocol):

    'Outputs task conclusions to the active user interface.'
    def speak(self, text: str) -> None: ...


class AssemblyExecutorPort(Protocol):

    'Receives validated semantic steps for ARCS execution.'
    def execute_step(
        self,
        step: AssemblyStep,
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
    ) -> None: ...


class CompetitionWorkflow:

    'Runs task 1, task 2, and the assembly procedure automatically.'
    def __init__(
        self,
        *,
        task_input: TaskInputPort,
        agent: IntelligentAgentPort,
        speaker: SpeakerPort,
        executor: AssemblyExecutorPort,
        logger: AuditLogger,
    ) -> None:
        self._task_input = task_input
        self._agent = agent
        self._speaker = speaker
        self._executor = executor
        self._logger = logger
        self._state = TaskState.IDLE

    @property
    def state(self) -> TaskState:
        return self._state

    def run(self) -> None:
        self._run_tasks()

    def _run_tasks(self) -> None:
        self._transition(TaskState.TASK1_RECOGNIZING)
        card1 = self._task_input.get_card(1)
        scene = self._agent.summarize_scene(card1)
        self._log(
            "model_scene_result",
            "智能体完成任务卡 1 场景解析。",
            {"summary": scene.summary, "model_trace": scene.model_trace},
        )
        self._speak(scene.summary)

        self._transition(TaskState.TASK1_DONE)
        self._speak(TASK1_COMPLETED_RESPONSE)

        self._transition(TaskState.TASK2_RECOGNIZING)
        card2 = self._task_input.get_card(2)
        plan = self._agent.build_assembly_plan(card2)
        measurements = self._task_input.get_measurements(plan.steps)
        validate_task_plan(plan, measurements)
        self._log(
            "model_assembly_plan",
            "智能体完成任务卡 2 装配解析。",
            {
                "instruction": plan.task2_instruction,
                "model_trace": plan.model_trace,
                "triggers": [step.agent_trigger for step in plan.steps],
            },
        )
        self._speak(plan.task2_instruction)

        self._transition(TaskState.ASSEMBLING)
        for step in plan.steps:
            self._log(
                "assembly_step",
                f"开始执行第 {step.index} 步装配。",
                {
                    "index": step.index,
                    "block_color": step.block_color.display_name,
                    "tray_color": step.tray_color.display_name,
                    "agent_trigger": step.agent_trigger,
                },
            )
            self._executor.execute_step(step, measurements)

        self._transition(TaskState.COMPLETED)
        self._speak(ALL_TASKS_COMPLETED_RESPONSE)

    def _transition(self, next_state: TaskState) -> None:
        self._state = next_state
        self._log("state_transition", "比赛流程状态变更。", {"next_state": next_state.value})

    def _speak(self, text: str) -> None:
        self._speaker.speak(text)
        self._log("text_output", "显示固定文本或任务结果。", {"text": text})

    def _log(self, event_type: str, message: str, data: dict[str, object]) -> None:
        self._logger.write(
            AuditEvent(
                occurred_at=datetime.now().astimezone(),
                event_type=event_type,
                state=self._state,
                message=message,
                data=data,
            )
        )
