from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from .audit import AuditLogger
from .competition_logs import CompetitionEvidenceStore
from .models import AuditEvent, SceneResult, TaskPlan, TaskState
from .offline_state import OfflineRoundStateMachine, OfflineTaskKind
from .robot_process import AssemblyStepStateMachine
from .task_plan import validate_offline_task_plan
from .visionmaster_tcp import ReceivedTaskImage


class CardCollectorPort(Protocol):
    def collect_card(self, card_number: int) -> ReceivedTaskImage: ...


class VisionAgentPort(Protocol):
    def analyze_task1(self, image: ReceivedTaskImage) -> SceneResult: ...
    def analyze_task2(self, image: ReceivedTaskImage, *, task1_summary: str = "") -> TaskPlan: ...


class PhysicalRobotPort(Protocol):
    def prepare_round(self) -> None: ...
    def move_task_card_photo(self, slot_number: int) -> None: ...
    def finish_round(self) -> None: ...


class CurrentTaskCardReader:
    def __init__(
        self,
        collector: CardCollectorPort,
        evidence: CompetitionEvidenceStore,
    ) -> None:
        self._collector = collector
        self._evidence = evidence

    def read(self, card_number: int) -> ReceivedTaskImage:
        request_text = "duqu,99"
        requested_at = datetime.now().astimezone()
        try:
            image = self._collector.collect_card(card_number)
        except Exception as exc:
            self._evidence.record_vm(
                requested_at=requested_at,
                received_at=datetime.now().astimezone(),
                request_text=request_text,
                response_text=str(exc),
                status="失败",
            )
            raise
        self._evidence.record_vm(
            requested_at=requested_at,
            received_at=image.received_at,
            request_text=request_text,
            response_text=f"任务卡{card_number}最新图片读取成功，共{len(image.data)}字节",
            status="成功",
        )
        return image


class OfflineRoundWorkflow:
    def __init__(
        self,
        *,
        cards: CurrentTaskCardReader,
        model: VisionAgentPort,
        step_process: AssemblyStepStateMachine,
        robot: PhysicalRobotPort,
        evidence: CompetitionEvidenceStore,
        logger: AuditLogger,
        task_sequence: tuple[OfflineTaskKind, ...]
        | Callable[[], tuple[OfflineTaskKind, ...]],
        state: OfflineRoundStateMachine | None = None,
        preflight: Callable[[], object] | None = None,
        result_report: Callable[[str], None] | None = None,
        after_model_result: Callable[[], None] | None = None,
        check_cancelled: Callable[[], None] = lambda: None,
        progress: Callable[[dict], None] = lambda _event: None,
    ) -> None:
        self._cards = cards
        self._model = model
        self._step_process = step_process
        self._robot = robot
        self._evidence = evidence
        self._logger = logger
        self._task_sequence = task_sequence
        initial_sequence = task_sequence() if callable(task_sequence) else task_sequence
        self._state = state or OfflineRoundStateMachine(initial_sequence)
        self._external_state = state is not None
        self._preflight = preflight
        self._result_report = result_report or (lambda _text: None)
        self._after_model_result = after_model_result or (lambda: None)
        self._check_cancelled = check_cancelled
        self._progress = progress

    @property
    def state(self) -> OfflineRoundStateMachine:
        return self._state

    def run(self) -> None:
        robot_started = False
        try:
            if self._preflight is not None:
                self._preflight()
            robot_started = True
            self._robot.prepare_round()
            task_sequence = (
                self._task_sequence()
                if callable(self._task_sequence)
                else self._task_sequence
            )
            if not self._external_state:
                self._state = OfflineRoundStateMachine(task_sequence)
            for task in task_sequence:
                self._check_cancelled()
                self._progress({"phase": "photo", "task": task.value, "status": "运行中"})
                self._robot.move_task_card_photo(1)
                self._check_cancelled()
                self._progress({"phase": "photo", "task": task.value, "status": "已完成"})
                self._progress({"phase": "capture", "task": task.value, "status": "运行中"})
                image = self._cards.read(1 if task is OfflineTaskKind.TASK1 else 2)
                self._check_cancelled()
                self._progress({"phase": "capture", "task": task.value, "status": "已完成"})
                self._state.begin_task(task)
                self._progress({"phase": "recognize", "task": task.value, "status": "运行中"})
                if task is OfflineTaskKind.TASK1:
                    self._run_task1(image)
                else:
                    self._run_task2(image)
                self._state.complete_active_task()
                self._progress({"phase": "task_done", "task": task.value, "status": "已完成"})
        except Exception as exc:
            self._state.fail_round(str(exc))
            raise
        finally:
            if robot_started and self._state.snapshot().phase.value == "completed":
                self._robot.finish_round()

    def _run_task1(self, image: ReceivedTaskImage) -> None:
        result = self._model.analyze_task1(image)
        self._check_cancelled()
        self._evidence.record_task1(result)
        self._result_report("任务卡1识别结果：" + "、".join(result.objects))
        self._log(
            "offline_task1_result",
            "任务一卡片识别完成。",
            {
                "summary": result.summary,
                "objects": list(result.objects),
                "reasoning_text": result.reasoning_text,
                "model_trace": result.model_trace,
            },
        )
        self._after_model_result()
        self._progress({"phase": "recognize", "task": "task1", "status": "已完成"})

    def _run_task2(self, image: ReceivedTaskImage) -> None:
        plan = validate_offline_task_plan(self._model.analyze_task2(image))
        self._check_cancelled()
        self._evidence.record_task2(plan)
        self._result_report("任务卡2装配内容：" + plan.task2_instruction)
        self._log(
            "offline_task2_plan",
            "任务二装配顺序解析完成。",
            {
                "instruction": plan.task2_instruction,
                "model_trace": plan.model_trace,
                "triggers": [step.agent_trigger for step in plan.steps],
            },
        )
        self._after_model_result()
        self._progress({"phase": "recognize", "task": "task2", "status": "已完成"})
        self._progress({"phase": "plan", "steps": [
            {
                "index": step.index,
                "block": step.block_color.display_name,
                "target": step.destination_color.display_name,
                "destination_kind": step.destination_kind.value,
            }
            for step in plan.steps
        ]})
        for step in plan.steps:
            self._check_cancelled()
            self._step_process.run(step)
            self._evidence.mark_step_completed(step.index)

    def _log(self, event_type: str, message: str, data: dict[str, object]) -> None:
        self._logger.write(
            AuditEvent(
                occurred_at=datetime.now().astimezone(),
                event_type=event_type,
                state=TaskState.ASSEMBLING,
                message=message,
                data=data,
            )
        )
