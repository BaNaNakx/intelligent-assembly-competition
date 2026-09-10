from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OfflineTaskKind(str, Enum):
    TASK1 = "task1"
    TASK2 = "task2"


class OfflineRoundPhase(str, Enum):
    READY = "ready"
    EXECUTING_TASK = "executing_task"
    FAILED = "failed"
    COMPLETED = "completed"


class OfflineRoundStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OfflineRoundSnapshot:
    round_index: int
    phase: OfflineRoundPhase
    active_task: OfflineTaskKind | None
    completed_tasks: frozenset[OfflineTaskKind]
    failure_reason: str | None


class OfflineRoundStateMachine:
    def __init__(
        self,
        target_tasks: tuple[OfflineTaskKind, ...] = tuple(OfflineTaskKind),
    ) -> None:
        if not target_tasks or len(set(target_tasks)) != len(target_tasks):
            raise ValueError("目标任务必须非空且不能重复。")
        self._round_index = 1
        self._target_tasks = frozenset(target_tasks)
        self._phase = OfflineRoundPhase.READY
        self._active_task: OfflineTaskKind | None = None
        self._completed_tasks: set[OfflineTaskKind] = set()
        self._failure_reason: str | None = None

    def snapshot(self) -> OfflineRoundSnapshot:
        return OfflineRoundSnapshot(
            self._round_index,
            self._phase,
            self._active_task,
            frozenset(self._completed_tasks),
            self._failure_reason,
        )

    def begin_task(self, task: OfflineTaskKind) -> None:
        if self._phase is not OfflineRoundPhase.READY:
            raise OfflineRoundStateError("当前状态不能启动新任务。")
        if task not in self._target_tasks:
            raise OfflineRoundStateError("当前测试流程未启用该任务。")
        if task in self._completed_tasks:
            raise OfflineRoundStateError("当前轮次中该任务已经完成。")
        self._active_task = task
        self._phase = OfflineRoundPhase.EXECUTING_TASK

    def complete_active_task(self) -> OfflineRoundPhase:
        if (
            self._phase is not OfflineRoundPhase.EXECUTING_TASK
            or self._active_task is None
        ):
            raise OfflineRoundStateError("当前没有正在执行的任务。")
        self._completed_tasks.add(self._active_task)
        self._active_task = None
        if self._completed_tasks == self._target_tasks:
            self._phase = OfflineRoundPhase.COMPLETED
        else:
            self._phase = OfflineRoundPhase.READY
        return self._phase

    def fail_round(self, reason: str) -> None:
        normalized = reason.strip()
        if not normalized:
            raise ValueError("失败原因不能为空。")
        if self._phase is OfflineRoundPhase.COMPLETED:
            raise OfflineRoundStateError("已完成轮次不能再标记失败。")
        self._active_task = None
        self._completed_tasks.clear()
        self._failure_reason = normalized
        self._phase = OfflineRoundPhase.FAILED

    def restart_round(self) -> None:
        if self._phase is not OfflineRoundPhase.FAILED:
            raise OfflineRoundStateError("只有失败轮次可以重新开始。")
        self._round_index += 1
        self._phase = OfflineRoundPhase.READY
        self._active_task = None
        self._completed_tasks.clear()
        self._failure_reason = None
