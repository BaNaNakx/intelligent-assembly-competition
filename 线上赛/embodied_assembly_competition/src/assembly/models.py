'Stable domain data shared by all integration adapters.'

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, TypeAlias


class Color(str, Enum):
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"

    @property
    def display_name(self) -> str:
        return _COLOR_DISPLAY_NAMES[self]

    @property
    def block_trigger(self) -> str:
        return _BLOCK_TRIGGERS[self]

    @property
    def tray_trigger(self) -> str:
        return _TRAY_TRIGGERS[self]


_COLOR_DISPLAY_NAMES: Mapping[Color, str] = {
    Color.RED: "红色",
    Color.ORANGE: "橙色",
    Color.YELLOW: "黄色",
    Color.GREEN: "绿色",
    Color.BLUE: "蓝色",
    Color.PURPLE: "紫色",
}

_BLOCK_TRIGGERS: Mapping[Color, str] = {
    Color.RED: "11",
    Color.ORANGE: "12",
    Color.YELLOW: "13",
    Color.GREEN: "14",
    Color.BLUE: "15",
    Color.PURPLE: "16",
}

_TRAY_TRIGGERS: Mapping[Color, str] = {
    Color.RED: "21",
    Color.ORANGE: "22",
    Color.YELLOW: "23",
    Color.GREEN: "24",
    Color.BLUE: "25",
    Color.PURPLE: "26",
}


class EntityKind(str, Enum):
    BLOCK = "block"
    TRAY = "tray"


class TaskState(str, Enum):
    IDLE = "idle"
    TASK1_RECOGNIZING = "task1_recognizing"
    TASK1_DONE = "task1_done"
    TASK2_RECOGNIZING = "task2_recognizing"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class VisionMeasurement:

    'One fixed VisionMaster coordinate packet from the project specification.'
    color: Color
    kind: EntityKind
    x: float
    y: float
    rz: float
    raw_packet: str
    received_at: datetime

    @property
    def key(self) -> tuple[Color, EntityKind]:
        return (self.color, self.kind)


@dataclass(frozen=True, slots=True)
class TaskCard:

    'A normalized task-card result supplied by the VisionMaster adapter.'
    number: int
    scene_objects: tuple[str, ...] = ()
    instruction_text: str | None = None
    recognized_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if self.number not in (1, 2):
            raise ValueError("任务卡编号只能是 1 或 2。")
        if self.number == 1 and not self.scene_objects:
            raise ValueError("任务卡 1 必须包含至少一个场景物体。")
        if self.number == 2 and not self.instruction_text:
            raise ValueError("任务卡 2 必须包含装配指令。")


@dataclass(frozen=True, slots=True)
class AssemblyStep:

    'One validated block-to-tray assembly instruction.'
    index: int
    block_color: Color
    tray_color: Color
    block_trigger: str
    tray_trigger: str

    @property
    def agent_trigger(self) -> str:
        return f"{self.block_trigger} {self.tray_trigger}"

    @classmethod
    def from_colors(
        cls, index: int, block_color: Color, tray_color: Color
    ) -> "AssemblyStep":
        return cls(
            index=index,
            block_color=block_color,
            tray_color=tray_color,
            block_trigger=block_color.block_trigger,
            tray_trigger=tray_color.tray_trigger,
        )


@dataclass(frozen=True, slots=True)
class TaskPlan:

    'Structured result returned by the LLM adapter and validated before motion.'
    task1_summary: str
    task2_instruction: str
    steps: tuple[AssemblyStep, ...]
    model_trace: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class SceneResult:

    'A displayable scene conclusion returned by the intelligent agent.'
    summary: str
    model_trace: str
    objects: tuple[str, ...] = ()
    reasoning_text: str = ""
    scene_category: str = ""
    created_at: datetime = field(default_factory=datetime.now)


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    occurred_at: datetime
    event_type: str
    state: TaskState
    message: str
    data: Mapping[str, JSONValue]
