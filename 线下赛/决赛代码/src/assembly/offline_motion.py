'Physical-robot calibration, safety bounds, and offline assembly motion.'

from __future__ import annotations

from collections.abc import Mapping, Callable
from dataclasses import dataclass
from enum import Enum
from math import radians, sqrt
from typing import Protocol

from .arcs_jsonrpc import ArcsMotionSafetyStatus
from .models import AssemblyStep, Color, EntityKind, VisionMeasurement
from .motion_plan import ToolPose
from .robot_parameters import (
    ROBOT_GLOBAL_PARAMETERS,
    RobotGlobalParameters,
    RobotParameterStore,
)


ZERO_JOINTS_RAD = ROBOT_GLOBAL_PARAMETERS.zero_joints_rad
DEFAULT_BLOCK_PHOTO_JOINTS_RAD = ROBOT_GLOBAL_PARAMETERS.block_photo_joints_rad
DEFAULT_TOOL_RX_RAD = radians(ROBOT_GLOBAL_PARAMETERS.tool_rx_deg)
DEFAULT_TOOL_RY_RAD = radians(ROBOT_GLOBAL_PARAMETERS.tool_ry_deg)
BASE_PLANE_Z_M = ROBOT_GLOBAL_PARAMETERS.base_plane_z_mm / 1000.0
PICKUP_Z_M = ROBOT_GLOBAL_PARAMETERS.block_pick_z_mm / 1000.0
PLACE_Z_M = ROBOT_GLOBAL_PARAMETERS.block_place_z_mm / 1000.0
STACK_Z_M = ROBOT_GLOBAL_PARAMETERS.stack_z_mm / 1000.0
ES5_MAX_REACH_M = 0.8865
PRECISION_LINE_LABELS = frozenset({"pick_arrive", "place_arrive", "stack_arrive"})


class PhysicalMotionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlanarVmCalibration:
    x_from_x: float
    x_from_y: float
    x_offset_mm: float
    y_from_x: float
    y_from_y: float
    y_offset_mm: float
    rz_sign: float
    rz_offset_rad: float
    validated: bool = False

    @classmethod
    def robot_base_identity(cls) -> "PlanarVmCalibration":
        return cls(
            x_from_x=1.0,
            x_from_y=0.0,
            x_offset_mm=0.0,
            y_from_x=0.0,
            y_from_y=1.0,
            y_offset_mm=0.0,
            rz_sign=1.0,
            rz_offset_rad=0.0,
            validated=True,
        )

    def transform_xy_mm(self, x_vm: float, y_vm: float) -> tuple[float, float]:
        if not self.validated:
            raise PhysicalMotionError("相机到机器人基坐标的标定参数尚未验证。")
        return (
            self.x_from_x * x_vm + self.x_from_y * y_vm + self.x_offset_mm,
            self.y_from_x * x_vm + self.y_from_y * y_vm + self.y_offset_mm,
        )


@dataclass(frozen=True, slots=True)
class CartesianWorkspace:
    min_x_m: float
    max_x_m: float
    min_y_m: float
    max_y_m: float
    min_z_m: float
    max_z_m: float
    max_planar_radius_m: float | None = None

    @classmethod
    def for_es5(cls) -> "CartesianWorkspace":
        return cls(
            -ES5_MAX_REACH_M,
            ES5_MAX_REACH_M,
            -ES5_MAX_REACH_M,
            ES5_MAX_REACH_M,
            BASE_PLANE_Z_M,
            ES5_MAX_REACH_M,
            ES5_MAX_REACH_M,
        )

    @classmethod
    def from_parameters(cls, parameters: RobotGlobalParameters) -> "CartesianWorkspace":
        return cls(
            parameters.workspace_min_x_mm / 1000.0,
            parameters.workspace_max_x_mm / 1000.0,
            parameters.workspace_min_y_mm / 1000.0,
            parameters.workspace_max_y_mm / 1000.0,
            parameters.workspace_min_z_mm / 1000.0,
            parameters.workspace_max_z_mm / 1000.0,
            parameters.workspace_radius_mm / 1000.0,
        )

    def require_contains(self, pose: ToolPose) -> None:
        if not (
            self.min_x_m <= pose.x_m <= self.max_x_m
            and self.min_y_m <= pose.y_m <= self.max_y_m
            and self.min_z_m <= pose.z_m <= self.max_z_m
        ):
            raise PhysicalMotionError("目标位姿超出已标定的线下赛安全工作空间。")
        if (
            self.max_planar_radius_m is not None
            and sqrt(pose.x_m * pose.x_m + pose.y_m * pose.y_m)
            > self.max_planar_radius_m
        ):
            raise PhysicalMotionError("目标位姿超出 AUBO ES5 的最大平面臂展。")


@dataclass(frozen=True, slots=True)
class PhysicalAssemblyGeometry:
    calibration: PlanarVmCalibration
    workspace: CartesianWorkspace
    pickup_z_m: float
    place_z_m: float
    stack_z_m: float
    fixed_rx_rad: float
    fixed_ry_rad: float
    block_photo_joints_rad: tuple[float, float, float, float, float, float] = DEFAULT_BLOCK_PHOTO_JOINTS_RAD
    base_plane_z_m: float = BASE_PLANE_Z_M
    vm_xy_scale_k: float = 1.0
    tcp_delta_xy_m: tuple[float, float] = (-0.01353, -0.12219)
    lift_distance_m: float = 0.1

    @classmethod
    def from_robot_base_coordinates(
        cls,
        *,
        workspace: CartesianWorkspace,
        pickup_z_m: float = PICKUP_Z_M,
        place_z_m: float = PLACE_Z_M,
        stack_z_m: float = STACK_Z_M,
        fixed_rx_rad: float = DEFAULT_TOOL_RX_RAD,
        fixed_ry_rad: float = DEFAULT_TOOL_RY_RAD,
        block_photo_joints_rad: tuple[
            float, float, float, float, float, float
        ] = DEFAULT_BLOCK_PHOTO_JOINTS_RAD,
        vm_xy_scale_k: float = 1.0,
        tcp_delta_xy_m: tuple[float, float] = (-0.01353, -0.12219),
    ) -> "PhysicalAssemblyGeometry":
        return cls(
            calibration=PlanarVmCalibration.robot_base_identity(),
            workspace=workspace,
            pickup_z_m=pickup_z_m,
            place_z_m=place_z_m,
            stack_z_m=stack_z_m,
            fixed_rx_rad=fixed_rx_rad,
            fixed_ry_rad=fixed_ry_rad,
            block_photo_joints_rad=block_photo_joints_rad,
            base_plane_z_m=BASE_PLANE_Z_M,
            vm_xy_scale_k=vm_xy_scale_k,
            tcp_delta_xy_m=tcp_delta_xy_m,
        )

    @classmethod
    def from_parameters(
        cls, parameters: RobotGlobalParameters
    ) -> "PhysicalAssemblyGeometry":
        return cls(
            calibration=PlanarVmCalibration(
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                parameters.rz_sign,
                radians(parameters.rz_offset_deg),
                True,
            ),
            workspace=CartesianWorkspace.from_parameters(parameters),
            pickup_z_m=parameters.block_pick_z_mm / 1000.0,
            place_z_m=parameters.block_place_z_mm / 1000.0,
            stack_z_m=parameters.stack_z_mm / 1000.0,
            fixed_rx_rad=radians(parameters.tool_rx_deg),
            fixed_ry_rad=radians(parameters.tool_ry_deg),
            block_photo_joints_rad=parameters.block_photo_joints_rad,
            base_plane_z_m=parameters.base_plane_z_mm / 1000.0,
            vm_xy_scale_k=parameters.vm_xy_scale_k,
            tcp_delta_xy_m=tuple(value / 1000.0 for value in parameters.tcp_delta_xy_mm),
            lift_distance_m=parameters.lift_distance_mm / 1000.0,
        )

    def __post_init__(self) -> None:
        heights = (self.pickup_z_m, self.place_z_m, self.stack_z_m)
        if min(heights) < self.base_plane_z_m:
            raise ValueError("抓取高度和放置高度不能低于基座平面。")
        if self.lift_distance_m <= 0:
            raise ValueError("相对抬升距离必须大于零。")
        if len(self.block_photo_joints_rad) != 6:
            raise ValueError("物块拍照位必须包含六个关节角。")
        if (
            len(self.tcp_delta_xy_m) != 2
        ):
            raise ValueError("TCP-Delta 必须只包含 X、Y。")
        if self.vm_xy_scale_k <= 0:
            raise ValueError("VM X/Y 偏移比例 k 必须大于0。")

    def block_height_m(self, color: Color) -> float:
        return self.pickup_z_m

    def to_pose(
        self,
        measurement: VisionMeasurement,
        z_m: float,
        origin_pose_m_rad: tuple[float, float, float, float, float, float]
        | None = None,
    ) -> ToolPose:
        x_mm, y_mm = self.calibration.transform_xy_mm(measurement.x, measurement.y)
        origin = origin_pose_m_rad or (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        pose = ToolPose(
            origin[0] + self.vm_xy_scale_k * x_mm / 1000.0,
            origin[1] + self.vm_xy_scale_k * y_mm / 1000.0,
            z_m,
            origin[3] if origin_pose_m_rad is not None else self.fixed_rx_rad,
            origin[4] if origin_pose_m_rad is not None else self.fixed_ry_rad,
            origin[5]
            + self.calibration.rz_offset_rad
            + radians(measurement.rz) * self.calibration.rz_sign,
        )
        self.workspace.require_contains(pose)
        return pose

    def to_robot_centering_pose(
        self,
        camera_pose: ToolPose,
    ) -> ToolPose:
        pose = ToolPose(
            camera_pose.x_m + self.tcp_delta_xy_m[0],
            camera_pose.y_m + self.tcp_delta_xy_m[1],
            camera_pose.z_m,
            camera_pose.rx_rad,
            camera_pose.ry_rad,
            camera_pose.rz_rad,
        )
        self.workspace.require_contains(pose)
        return pose


class PhysicalCommandKind(str, Enum):
    MOVE_JOINT = "move_joint"
    MOVE_LINE = "move_line"
    SUCTION_ON = "suction_on"
    SUCTION_OFF = "suction_off"


@dataclass(frozen=True, slots=True)
class PhysicalMotionCommand:
    kind: PhysicalCommandKind
    label: str
    pose: ToolPose | None = None
    joints_rad: tuple[float, float, float, float, float, float] | None = None
    lift_distance_m: float | None = None


@dataclass(frozen=True, slots=True)
class RecordedPlacementPose:
    color: Color
    pose1: ToolPose
    pose2: ToolPose


class PhysicalAssemblyMotionPlanner:
    def __init__(self, geometry: PhysicalAssemblyGeometry) -> None:
        self.geometry = geometry

    def plan_step(
        self,
        step: AssemblyStep,
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
        block_origin=None,
        tray_origin=None,
    ) -> tuple[PhysicalMotionCommand, ...]:
        source = measurements[(step.block_color, EntityKind.BLOCK)]
        destination = measurements[(step.tray_color, EntityKind.TRAY)]
        return (
            *self.plan_pick(source, block_origin),
            *self.plan_place(destination, step.block_color, tray_origin),
            self.plan_return(),
        )

    def plan_pick(
        self,
        source: VisionMeasurement,
        origin_pose_m_rad: tuple[float, float, float, float, float, float]
        | None = None,
    ) -> tuple[PhysicalMotionCommand, ...]:
        return self._plan_alignment(source, origin_pose_m_rad, "pick", self.geometry.pickup_z_m)

    def plan_place(
        self,
        destination: VisionMeasurement,
        block_color: Color | None = None,
        origin_pose_m_rad: tuple[float, float, float, float, float, float]
        | None = None,
    ) -> tuple[PhysicalMotionCommand, ...]:
        return self._plan_alignment(destination, origin_pose_m_rad, "place", self.geometry.place_z_m)

    def plan_stack(
        self,
        saved_pose2_m_rad: tuple[float, float, float, float, float, float],
    ) -> tuple[PhysicalMotionCommand, ...]:
        above = ToolPose(*saved_pose2_m_rad)
        arrive = above.with_z(self.geometry.stack_z_m)
        if above.z_m <= arrive.z_m:
            raise PhysicalMotionError("物块装配位姿2必须高于叠放高度。")
        for pose in (above, arrive):
            self.geometry.workspace.require_contains(pose)
        return (
            PhysicalMotionCommand(PhysicalCommandKind.MOVE_LINE, "stack_above", above),
            PhysicalMotionCommand(PhysicalCommandKind.MOVE_LINE, "stack_arrive", arrive),
            PhysicalMotionCommand(PhysicalCommandKind.SUCTION_OFF, "suction_off"),
            PhysicalMotionCommand(
                PhysicalCommandKind.MOVE_LINE,
                "stack_retreat",
                arrive.with_z(arrive.z_m + self.geometry.lift_distance_m),
                lift_distance_m=self.geometry.lift_distance_m,
            ),
        )

    def _plan_alignment(self, measurement, origin, prefix, height):
        if origin is None:
            raise PhysicalMotionError("对准运动必须提供本次拍照时的实际 TCP 位姿。")
        if origin[2] <= height:
            raise PhysicalMotionError("拍照高度必须高于抓取/放置高度，禁止低位横移。")
        camera = self.geometry.to_pose(measurement, origin[2], origin)
        suction = self.geometry.to_robot_centering_pose(camera)
        arrive = suction.with_z(height)
        retreat = arrive.with_z(height + self.geometry.lift_distance_m)
        for pose in (camera, suction, arrive, retreat):
            self.geometry.workspace.require_contains(pose)
        io_kind = PhysicalCommandKind.SUCTION_ON if prefix == "pick" else PhysicalCommandKind.SUCTION_OFF
        return (
            PhysicalMotionCommand(PhysicalCommandKind.MOVE_LINE, f"{prefix}_approach", camera),
            PhysicalMotionCommand(PhysicalCommandKind.MOVE_LINE, f"{prefix}_tcp_align", suction),
            PhysicalMotionCommand(PhysicalCommandKind.MOVE_LINE, f"{prefix}_arrive", arrive),
            PhysicalMotionCommand(io_kind, io_kind.value),
            PhysicalMotionCommand(PhysicalCommandKind.MOVE_LINE, f"{prefix}_retreat", retreat,
                                  lift_distance_m=self.geometry.lift_distance_m),
        )

    def plan_return(self) -> PhysicalMotionCommand:
        return PhysicalMotionCommand(
            PhysicalCommandKind.MOVE_JOINT,
            "return_block_photo_pose",
            joints_rad=self.geometry.block_photo_joints_rad,
        )


class PhysicalArcsPort(Protocol):
    def get_motion_safety_status(self) -> ArcsMotionSafetyStatus: ...
    def get_tcp_pose(self) -> tuple[float, float, float, float, float, float]: ...
    def move_joint(self, joints_rad, *, acceleration_rad_s2: float, velocity_rad_s: float) -> int: ...
    def move_line(self, pose_m_rad, *, acceleration_m_s2: float, velocity_m_s: float) -> int: ...
    def wait_until_steady(self, *, timeout_s: float, poll_interval_s: float) -> None: ...
    def emergency_stop(self) -> int: ...
    def trace_textmsg(self, message: str) -> int: ...


class PhysicalIoPort(Protocol):
    def set_suction(self, enabled: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class PhysicalMotionExecutionConfig:
    joint_acceleration_rad_s2: float = radians(30.0)
    joint_velocity_rad_s: float = radians(60.0)
    line_acceleration_m_s2: float = 0.4
    line_velocity_m_s: float = 0.4
    completion_timeout_s: float = 60.0
    poll_interval_s: float = 0.10
    precision_line_acceleration_m_s2: float = 0.3
    precision_line_velocity_m_s: float = 0.3

    def __post_init__(self) -> None:
        if min(
            self.joint_acceleration_rad_s2,
            self.joint_velocity_rad_s,
            self.line_acceleration_m_s2,
            self.line_velocity_m_s,
            self.completion_timeout_s,
            self.poll_interval_s,
            self.precision_line_acceleration_m_s2,
            self.precision_line_velocity_m_s,
        ) <= 0:
            raise ValueError("物理机械臂运动参数必须全部大于 0。")

    @classmethod
    def from_parameters(
        cls, parameters: RobotGlobalParameters
    ) -> "PhysicalMotionExecutionConfig":
        return cls(
            parameters.joint_acceleration_rad_s2,
            parameters.joint_velocity_rad_s,
            parameters.transit_acceleration_m_s2,
            parameters.transit_velocity_m_s,
            parameters.completion_timeout_s,
            parameters.poll_interval_s,
            parameters.precision_acceleration_m_s2,
            parameters.precision_velocity_m_s,
        )


def estimate_es5_six_step_motion_seconds(
    config: PhysicalMotionExecutionConfig = PhysicalMotionExecutionConfig(),
) -> float:
    maximum_planar_move_m = ES5_MAX_REACH_M * 2.0
    vertical_move_m = ROBOT_GLOBAL_PARAMETERS.block_photo_pose.z_mm / 1000.0 - PICKUP_Z_M
    transit = _trapezoid_time(
        maximum_planar_move_m,
        config.line_acceleration_m_s2,
        config.line_velocity_m_s,
    )
    transit_vertical = _trapezoid_time(
        vertical_move_m,
        config.line_acceleration_m_s2,
        config.line_velocity_m_s,
    )
    precision_vertical = _trapezoid_time(
        vertical_move_m,
        config.precision_line_acceleration_m_s2,
        config.precision_line_velocity_m_s,
    )
    per_step_s = (
        2.0 * transit
        + 2.0 * transit_vertical
        + 2.0 * precision_vertical
        + 6.0
        + 0.4
    )
    return 12.0 + 6.0 * per_step_s


def _trapezoid_time(distance_m: float, acceleration_m_s2: float, velocity_m_s: float) -> float:
    acceleration_distance = velocity_m_s * velocity_m_s / acceleration_m_s2
    if distance_m <= acceleration_distance:
        return 2.0 * sqrt(distance_m / acceleration_m_s2)
    return distance_m / velocity_m_s + velocity_m_s / acceleration_m_s2


class PhysicalAssemblyExecutor:
    def __init__(
        self,
        client: PhysicalArcsPort,
        io: PhysicalIoPort,
        planner: PhysicalAssemblyMotionPlanner,
        config: PhysicalMotionExecutionConfig,
        parameter_store: RobotParameterStore | None = None,
        check_cancelled: Callable[[], None] = lambda: None,
        submit: Callable[[Callable], object] = lambda action: action(),
        placement_pose_observer: Callable[[RecordedPlacementPose], None] = lambda _record: None,
    ) -> None:
        self._client = client
        self._io = io
        self._planner = planner
        self._config = config
        self._parameter_store = parameter_store
        self._check_cancelled = check_cancelled
        self._submit = submit
        self._placement_pose_observer = placement_pose_observer
        self._placement_poses: dict[Color, RecordedPlacementPose] = {}

    def prepare_round(self) -> None:
        self._assert_ready()

    def move_task_card_photo(self, slot_number: int) -> None:
        parameters = self._current_parameters()
        if slot_number == 1:
            joints = parameters.task_card_slot1_joints_rad
        elif slot_number == 2:
            joints = parameters.task_card_slot2_joints_rad
        else:
            raise ValueError("任务卡拍照位编号只能是1或2。")
        self._move_joint(joints)

    def move_block_photo(self) -> None:
        if self._parameter_store is None:
            joints = self._planner.geometry.block_photo_joints_rad
        else:
            joints = self._parameter_store.snapshot().block_photo_joints_rad
        self._move_joint(joints)

    def move_tray_photo(self) -> None:
        self._move_joint(self._current_parameters().tray_photo_joints_rad)

    def execute_step(
        self,
        step: AssemblyStep,
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
    ) -> None:
        source = measurements[(step.block_color, EntityKind.BLOCK)]
        destination = measurements[(step.tray_color, EntityKind.TRAY)]
        self.pick(step, source)
        self.place(step, destination)
        self.return_photo()

    def pick(self, step: AssemblyStep, source: VisionMeasurement) -> None:
        self._assert_ready()
        origin_pose = self._client.get_tcp_pose()
        try:
            for command in self._current_planner().plan_pick(source, origin_pose):
                self._execute(command)
        except Exception:
            self._client.emergency_stop()
            raise

    def place(self, step: AssemblyStep, destination: VisionMeasurement) -> None:
        self._assert_ready()
        origin_pose = self._client.get_tcp_pose()
        try:
            for command in self._current_planner().plan_place(destination, step.block_color, origin_pose):
                self._execute(command)
                if command.label == "place_arrive":
                    pose1 = ToolPose(*self._client.get_tcp_pose())
                    pose2 = pose1.with_z(
                        pose1.z_m + self._current_planner().geometry.lift_distance_m
                    )
                    self._current_planner().geometry.workspace.require_contains(pose2)
                    record = RecordedPlacementPose(step.block_color, pose1, pose2)
                    self._placement_poses[step.block_color] = record
                    self._placement_pose_observer(record)
        except Exception:
            self._client.emergency_stop()
            raise

    def stack(self, step: AssemblyStep) -> None:
        self._assert_ready()
        if not step.is_stack:
            raise PhysicalMotionError("只有方块叠放步骤可以调用叠放动作。")
        try:
            record = self._placement_poses[step.destination_color]
        except KeyError as exc:
            raise PhysicalMotionError(
                f"尚未记录{step.destination_color.display_name}物块装配位姿，禁止执行叠放。"
            ) from exc
        try:
            self.move_tray_photo()
            for command in self._current_planner().plan_stack(tuple(record.pose2.as_list())):
                self._execute(command)
        except Exception:
            self._client.emergency_stop()
            raise

    def placement_pose(self, color: Color) -> RecordedPlacementPose:
        try:
            return self._placement_poses[color]
        except KeyError as exc:
            raise PhysicalMotionError(f"尚未记录{color.display_name}物块装配位姿。") from exc

    def return_photo(self) -> None:
        self._assert_ready()
        try:
            self.move_block_photo()
        except Exception:
            self._client.emergency_stop()
            raise

    def _execute(self, command: PhysicalMotionCommand) -> None:
        config = self._current_config()
        self._assert_ready()
        self._check_cancelled()
        if command.kind is PhysicalCommandKind.MOVE_LINE:
            assert command.pose is not None
            pose = command.pose
            if command.lift_distance_m is not None:
                current = ToolPose(*self._client.get_tcp_pose())
                pose = current.with_z(current.z_m + command.lift_distance_m)
                self._current_planner().geometry.workspace.require_contains(pose)
                self._check_cancelled()
            precision = command.label in PRECISION_LINE_LABELS
            self._submit(lambda: self._client.move_line(
                pose.as_list(),
                acceleration_m_s2=(
                    config.precision_line_acceleration_m_s2
                    if precision
                    else config.line_acceleration_m_s2
                ),
                velocity_m_s=(
                    config.precision_line_velocity_m_s
                    if precision
                    else config.line_velocity_m_s
                ),
            ))
            self._wait()
        elif command.kind is PhysicalCommandKind.MOVE_JOINT:
            assert command.joints_rad is not None
            self._move_joint(command.joints_rad)
        elif command.kind is PhysicalCommandKind.SUCTION_ON:
            self._submit(lambda: self._io.set_suction(True))
        else:
            self._submit(lambda: self._io.set_suction(False))

    def finish_round(self) -> None:
        self._check_cancelled()
        self._submit(lambda: self._io.set_suction(False))

    def _move_joint(self, joints_rad) -> None:
        self._assert_ready()
        config = self._current_config()
        self._check_cancelled()
        self._submit(lambda: self._client.move_joint(
            joints_rad,
            acceleration_rad_s2=config.joint_acceleration_rad_s2,
            velocity_rad_s=config.joint_velocity_rad_s,
        ))
        self._wait()

    def _wait(self) -> None:
        config = self._current_config()
        self._client.wait_until_steady(
            timeout_s=config.completion_timeout_s,
            poll_interval_s=config.poll_interval_s,
        )
        self._check_cancelled()

    def _current_planner(self) -> PhysicalAssemblyMotionPlanner:
        if self._parameter_store is None:
            return self._planner
        return PhysicalAssemblyMotionPlanner(
            PhysicalAssemblyGeometry.from_parameters(self._parameter_store.snapshot())
        )

    def _current_config(self) -> PhysicalMotionExecutionConfig:
        if self._parameter_store is None:
            return self._config
        return PhysicalMotionExecutionConfig.from_parameters(
            self._parameter_store.snapshot()
        )

    def _current_parameters(self) -> RobotGlobalParameters:
        if self._parameter_store is None:
            return ROBOT_GLOBAL_PARAMETERS
        return self._parameter_store.snapshot()

    def _assert_ready(self) -> None:
        self._check_cancelled()
        status = self._client.get_motion_safety_status()
        if not status.powered_on or not status.within_safety_limits or status.collision_occurred:
            raise PhysicalMotionError("物理机械臂未满足上电、限位与无碰撞条件。")
