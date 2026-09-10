'Deterministic virtual assembly coordinate conversion and motion paths.'

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import pi, radians
from typing import Mapping

from .models import AssemblyStep, Color, EntityKind, VisionMeasurement


INITIAL_JOINTS_RAD = (0.0, 0.0, pi / 2, 0.0, pi / 2, 0.0)
INITIAL_TOOL_POSE_M_RAD = (0.4785, -0.1215, 0.50515, -pi, 0.0, pi / 2)


class MotionKind(str, Enum):
    MOVE_JOINT = "move_joint"
    MOVE_LINE = "move_line"


@dataclass(frozen=True, slots=True)
class ToolPose:

    'ARCS Cartesian target, expressed as metres and radians.'
    x_m: float
    y_m: float
    z_m: float
    rx_rad: float
    ry_rad: float
    rz_rad: float

    def with_z(self, z_m: float) -> "ToolPose":
        return ToolPose(
            self.x_m,
            self.y_m,
            z_m,
            self.rx_rad,
            self.ry_rad,
            self.rz_rad,
        )

    def as_list(self) -> list[float]:
        return [
            self.x_m,
            self.y_m,
            self.z_m,
            self.rx_rad,
            self.ry_rad,
            self.rz_rad,
        ]


@dataclass(frozen=True, slots=True)
class MotionCommand:

    'One ARCS move command; only the executor may send it to ARCS.'
    kind: MotionKind
    label: str
    pose: ToolPose | None = None
    joints_rad: tuple[float, float, float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.kind is MotionKind.MOVE_LINE and self.pose is None:
            raise ValueError("直线运动必须包含 TCP 目标位姿。")
        if self.kind is MotionKind.MOVE_JOINT and self.joints_rad is None:
            raise ValueError("关节运动必须包含六个关节角。")
        if self.pose is not None and self.joints_rad is not None:
            raise ValueError("一条运动命令不能同时包含 TCP 位姿和关节角。")


@dataclass(frozen=True, slots=True)
class VirtualAssemblyGeometry:

    'Applies VM millimetre offsets to the initial TCP at a 1:1 scale.'
    origin_x_m: float = INITIAL_TOOL_POSE_M_RAD[0]
    origin_y_m: float = INITIAL_TOOL_POSE_M_RAD[1]
    xy_scale_m_per_vm_mm: float = 0.001
    task_z_m: float = 0.010
    safe_z_m: float = 0.120
    fixed_rx_rad: float = -pi
    fixed_ry_rad: float = 0.0
    initial_rz_rad: float = pi / 2

    def __post_init__(self) -> None:
        if self.xy_scale_m_per_vm_mm <= 0:
            raise ValueError("VM 坐标缩放系数必须大于 0。")
        if self.task_z_m <= 0 or self.safe_z_m <= self.task_z_m:
            raise ValueError("安全高度必须高于任务到位高度。")


class VmToArcsCoordinateTransformer:

    'Converts project-book VM packets into ARCS Cartesian poses.'
    def __init__(self, geometry: VirtualAssemblyGeometry = VirtualAssemblyGeometry()) -> None:
        self._geometry = geometry

    @property
    def geometry(self) -> VirtualAssemblyGeometry:
        return self._geometry

    def to_task_pose(self, measurement: VisionMeasurement) -> ToolPose:
        return ToolPose(
            x_m=self._geometry.origin_x_m
            + measurement.x * self._geometry.xy_scale_m_per_vm_mm,
            y_m=self._geometry.origin_y_m
            + measurement.y * self._geometry.xy_scale_m_per_vm_mm,
            z_m=self._geometry.task_z_m,
            rx_rad=self._geometry.fixed_rx_rad,
            ry_rad=self._geometry.fixed_ry_rad,
            rz_rad=self._geometry.initial_rz_rad + radians(measurement.rz),
        )


class VirtualAssemblyMotionPlanner:

    'Builds one six-segment pick/place path followed by the required home move.'
    def __init__(
        self,
        transformer: VmToArcsCoordinateTransformer = VmToArcsCoordinateTransformer(),
    ) -> None:
        self._transformer = transformer

    def plan_step(
        self,
        step: AssemblyStep,
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
    ) -> tuple[MotionCommand, ...]:
        source = self._required_measurement(measurements, step.block_color, EntityKind.BLOCK)
        destination = self._required_measurement(
            measurements, step.tray_color, EntityKind.TRAY
        )
        pick = self._transformer.to_task_pose(source)
        place = self._transformer.to_task_pose(destination)
        safe_z = self._transformer.geometry.safe_z_m
        return (
            MotionCommand(MotionKind.MOVE_LINE, "pick_approach", pose=pick.with_z(safe_z)),
            MotionCommand(MotionKind.MOVE_LINE, "pick_arrive", pose=pick),
            MotionCommand(MotionKind.MOVE_LINE, "pick_retreat", pose=pick.with_z(safe_z)),
            MotionCommand(MotionKind.MOVE_LINE, "place_approach", pose=place.with_z(safe_z)),
            MotionCommand(MotionKind.MOVE_LINE, "place_arrive", pose=place),
            MotionCommand(MotionKind.MOVE_LINE, "place_retreat", pose=place.with_z(safe_z)),
            MotionCommand(
                MotionKind.MOVE_JOINT,
                "return_initial_pose",
                joints_rad=INITIAL_JOINTS_RAD,
            ),
        )

    @staticmethod
    def _required_measurement(
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
        color: Color,
        kind: EntityKind,
    ) -> VisionMeasurement:
        try:
            return measurements[(color, kind)]
        except KeyError as exc:
            raise ValueError(f"缺少 {color.display_name}{kind.value} 的 VisionMaster 数据。") from exc
