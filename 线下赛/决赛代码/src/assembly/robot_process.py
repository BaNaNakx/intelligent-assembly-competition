from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Callable
from typing import Protocol

from .models import AssemblyStep, VisionMeasurement


class StepAction(str, Enum):
    LOCATE_BLOCK = "locate_block"
    LOCATE_TRAY = "locate_tray"
    PICK = "pick"
    PLACE = "place"
    STACK = "stack"
    RETURN_PHOTO = "return_photo"


class StepProcessState(str, Enum):
    READY = "ready"
    LOCATING_BLOCK = "locating_block"
    LOCATING_TRAY = "locating_tray"
    PICKING = "picking"
    PLACING = "placing"
    STACKING = "stacking"
    RETURNING = "returning"
    COMPLETED = "completed"
    FAILED = "failed"


class StepVisionPort(Protocol):
    def locate_block(self, step: AssemblyStep) -> VisionMeasurement: ...
    def locate_tray(self, step: AssemblyStep) -> VisionMeasurement: ...


class StepRobotPort(Protocol):
    def move_block_photo(self) -> None: ...
    def move_tray_photo(self) -> None: ...
    def pick(self, step: AssemblyStep, measurement: VisionMeasurement) -> None: ...
    def place(self, step: AssemblyStep, measurement: VisionMeasurement) -> None: ...
    def stack(self, step: AssemblyStep) -> None: ...
    def return_photo(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StepProcessSnapshot:
    step_index: int
    state: StepProcessState
    completed_actions: tuple[StepAction, ...]
    error: str | None


class AssemblyStepStateMachine:
    def __init__(
        self,
        vision: StepVisionPort,
        robot: StepRobotPort,
        report,
        actions: tuple[StepAction, ...] | Callable[[], tuple[StepAction, ...]],
        check_cancelled: Callable[[], None] = lambda: None,
        progress: Callable[[dict], None] = lambda _event: None,
    ) -> None:
        self._vision = vision
        self._robot = robot
        self._report = report
        self._actions = actions
        self._check_cancelled = check_cancelled
        self._progress = progress
        self._snapshot = StepProcessSnapshot(0, StepProcessState.READY, (), None)

    @property
    def snapshot(self) -> StepProcessSnapshot:
        return self._snapshot

    def run(self, step: AssemblyStep) -> StepProcessSnapshot:
        block: VisionMeasurement | None = None
        tray: VisionMeasurement | None = None
        completed: list[StepAction] = []
        action = None
        try:
            actions = (
                (StepAction.LOCATE_BLOCK, StepAction.PICK, StepAction.STACK)
                if step.is_stack
                else (self._actions() if callable(self._actions) else self._actions)
            )
            for action in actions:
                self._check_cancelled()
                self._snapshot = StepProcessSnapshot(
                    step.index,
                    _state_for(action),
                    tuple(completed),
                    None,
                )
                self._report(f"步骤{step.index}状态：{action.value}")
                self._progress({"phase": "step", "index": step.index, "action": action.value, "status": "运行中"})
                if action is StepAction.LOCATE_BLOCK:
                    self._robot.move_block_photo()
                    self._check_cancelled()
                    block = self._vision.locate_block(step)
                elif action is StepAction.LOCATE_TRAY:
                    self._robot.move_tray_photo()
                    self._check_cancelled()
                    tray = self._vision.locate_tray(step)
                elif action is StepAction.PICK:
                    if block is None:
                        raise RuntimeError("抓取前没有物块定位结果。")
                    self._robot.pick(step, block)
                elif action is StepAction.PLACE:
                    if tray is None:
                        raise RuntimeError("放置前没有托盘定位结果。")
                    self._robot.place(step, tray)
                elif action is StepAction.STACK:
                    self._robot.stack(step)
                else:
                    self._robot.return_photo()
                completed.append(action)
                self._check_cancelled()
                self._progress({"phase": "step", "index": step.index, "action": action.value, "status": "已完成"})
        except Exception as exc:
            if action is not None:
                self._progress({"phase": "step", "index": step.index, "action": action.value, "status": "失败"})
            self._snapshot = StepProcessSnapshot(
                step.index,
                StepProcessState.FAILED,
                tuple(completed),
                str(exc),
            )
            raise
        self._snapshot = StepProcessSnapshot(
            step.index,
            StepProcessState.COMPLETED,
            tuple(completed),
            None,
        )
        return self._snapshot


def parse_step_actions(values: tuple[str, ...]) -> tuple[StepAction, ...]:
    return tuple(StepAction(value) for value in values)


def _state_for(action: StepAction) -> StepProcessState:
    return {
        StepAction.LOCATE_BLOCK: StepProcessState.LOCATING_BLOCK,
        StepAction.LOCATE_TRAY: StepProcessState.LOCATING_TRAY,
        StepAction.PICK: StepProcessState.PICKING,
        StepAction.PLACE: StepProcessState.PLACING,
        StepAction.STACK: StepProcessState.STACKING,
        StepAction.RETURN_PHOTO: StepProcessState.RETURNING,
    }[action]
