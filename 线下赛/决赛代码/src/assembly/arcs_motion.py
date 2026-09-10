'Safe ARCS execution adapter for virtual pick-and-place paths.'

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose
from typing import Protocol

from .arcs_jsonrpc import ArcsMotionSafetyStatus, ArcsRobotSnapshot
from .models import AssemblyStep, Color, EntityKind, VisionMeasurement
from .motion_plan import (
    INITIAL_JOINTS_RAD,
    INITIAL_TOOL_POSE_M_RAD,
    MotionKind,
    VirtualAssemblyMotionPlanner,
)


class ArcsMotionExecutionError(RuntimeError):
    'Raised before or during a virtual motion sequence that is unsafe.'
    pass


class ArcsMotionClientPort(Protocol):
    def get_motion_safety_status(self) -> ArcsMotionSafetyStatus: ...

    def get_snapshot(self) -> ArcsRobotSnapshot: ...

    def move_joint(
        self,
        joints_rad: tuple[float, float, float, float, float, float],
        *,
        acceleration_rad_s2: float,
        velocity_rad_s: float,
    ) -> int: ...

    def move_line(
        self,
        pose_m_rad: list[float],
        *,
        acceleration_m_s2: float,
        velocity_m_s: float,
    ) -> int: ...

    def wait_until_steady(self, *, timeout_s: float, poll_interval_s: float) -> None: ...

    def emergency_stop(self) -> int: ...

    def trace_textmsg(self, message: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ArcsMotionExecutionConfig:
    joint_acceleration_rad_s2: float = 23.590162
    joint_velocity_rad_s: float = 3.434405
    line_acceleration_m_s2: float = 14.154157
    line_velocity_m_s: float = 0.686881
    completion_timeout_s: float = 60.0
    poll_interval_s: float = 0.10

    def __post_init__(self) -> None:
        if min(
            self.joint_acceleration_rad_s2,
            self.joint_velocity_rad_s,
            self.line_acceleration_m_s2,
            self.line_velocity_m_s,
            self.completion_timeout_s,
            self.poll_interval_s,
        ) <= 0:
            raise ValueError("ARCS 运动参数必须全部大于 0。")


class ArcsAssemblyExecutor:

    'Executes one semantic task step and always returns to its initial joints.'
    def __init__(
        self,
        client: ArcsMotionClientPort,
        *,
        planner: VirtualAssemblyMotionPlanner = VirtualAssemblyMotionPlanner(),
        config: ArcsMotionExecutionConfig = ArcsMotionExecutionConfig(),
    ) -> None:
        self._client = client
        self._planner = planner
        self._config = config
        self._initial_pose_prepared = False

    def execute_step(
        self,
        step: AssemblyStep,
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
    ) -> None:
        self._assert_ready()
        if not self._initial_pose_prepared:
            self.prepare_initial_pose()
        source = measurements[(step.block_color, EntityKind.BLOCK)]
        destination = measurements[(step.tray_color, EntityKind.TRAY)]
        self._client.trace_textmsg(
            f"STEP {step.index} VM_RX BLOCK={source.raw_packet} TRAY={destination.raw_packet}"
        )
        self._client.trace_textmsg(
            f"STEP {step.index} AGENT_TRIGGER={step.agent_trigger}"
        )
        for command in self._planner.plan_step(step, measurements):
            self._assert_not_collided()
            if command.kind is MotionKind.MOVE_LINE:
                assert command.pose is not None
                self._client.move_line(
                    command.pose.as_list(),
                    acceleration_m_s2=self._config.line_acceleration_m_s2,
                    velocity_m_s=self._config.line_velocity_m_s,
                )
            else:
                assert command.joints_rad is not None
                self._client.move_joint(
                    command.joints_rad,
                    acceleration_rad_s2=self._config.joint_acceleration_rad_s2,
                    velocity_rad_s=self._config.joint_velocity_rad_s,
                )
            self._client.wait_until_steady(
                timeout_s=self._config.completion_timeout_s,
                poll_interval_s=self._config.poll_interval_s,
            )
        self._assert_not_collided()
        self._client.trace_textmsg(
            f"STEP {step.index} RETURN_INITIAL_TCP {self._initial_pose_text()}"
        )

    def prepare_initial_pose(self) -> None:

        'Put the arm at the mandated initial joints before the first step.'
        self._assert_ready()
        if not self._is_at_initial_joints():
            self._client.move_joint(
                INITIAL_JOINTS_RAD,
                acceleration_rad_s2=self._config.joint_acceleration_rad_s2,
                velocity_rad_s=self._config.joint_velocity_rad_s,
            )
            self._client.wait_until_steady(
                timeout_s=self._config.completion_timeout_s,
                poll_interval_s=self._config.poll_interval_s,
            )
        self._assert_not_collided()
        self._client.trace_textmsg(f"INITIAL_TCP {self._initial_pose_text()}")
        self._initial_pose_prepared = True

    def _is_at_initial_joints(self) -> bool:
        current_joints = self._client.get_snapshot().joint_positions_rad
        return all(
            isclose(current, expected, abs_tol=1e-4)
            for current, expected in zip(current_joints, INITIAL_JOINTS_RAD)
        )

    def _assert_ready(self) -> None:
        status = self._client.get_motion_safety_status()
        if not status.powered_on:
            raise ArcsMotionExecutionError("ARCS 机器人尚未上电，拒绝发送运动命令。")
        if not status.within_safety_limits:
            raise ArcsMotionExecutionError("ARCS 机器人不在安全限位内，拒绝发送运动命令。")
        if status.collision_occurred:
            raise ArcsMotionExecutionError("ARCS 已检测到碰撞，拒绝发送运动命令。")

    def _assert_not_collided(self) -> None:
        if self._client.get_motion_safety_status().collision_occurred:
            self._client.emergency_stop()
            raise ArcsMotionExecutionError("ARCS 仿真发生碰撞，已发送紧急停止。")

    @staticmethod
    def _initial_pose_text() -> str:
        x_m, y_m, z_m, rx_rad, ry_rad, rz_rad = INITIAL_TOOL_POSE_M_RAD
        return (
            f"X={x_m * 1000:.2f}mm Y={y_m * 1000:.2f}mm Z={z_m * 1000:.2f}mm "
            f"RX={rx_rad:.3f}rad RY={ry_rad:.3f}rad RZ={rz_rad:.3f}rad"
        )
