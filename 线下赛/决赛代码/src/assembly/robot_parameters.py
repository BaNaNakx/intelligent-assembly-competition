from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from math import isfinite, radians
from threading import Lock

from .models import Color


ALLOWED_TASKS = frozenset({"task1", "task2"})
ALLOWED_STEP_ACTIONS = frozenset(
    {"locate_block", "locate_tray", "pick", "place", "return_photo"}
)
REQUIRED_STEP_ACTIONS = frozenset(
    {"locate_block", "locate_tray", "pick", "place"}
)
DEFAULT_STEP_ACTIONS = (
    "locate_block",
    "pick",
    "locate_tray",
    "place",
    "return_photo",
)


@dataclass(frozen=True, slots=True)
class PhotoPoseParameters:
    x_mm: float
    y_mm: float
    z_mm: float
    rx_rad: float
    ry_rad: float
    rz_rad: float
    joints_deg: tuple[float, ...]

    def __post_init__(self) -> None:
        values = (
            self.x_mm,
            self.y_mm,
            self.z_mm,
            self.rx_rad,
            self.ry_rad,
            self.rz_rad,
            *self.joints_deg,
        )
        if len(self.joints_deg) != 6:
            raise ValueError("拍照位必须包含六个关节角。")
        if not all(isfinite(value) for value in values):
            raise ValueError("拍照位参数必须是有限数值。")

    @property
    def joints_rad(self) -> tuple[float, ...]:
        return tuple(radians(value) for value in self.joints_deg)

    def to_form_values(self, prefix: str) -> dict[str, str]:
        values = {
            f"{prefix}_x_mm": _number(self.x_mm),
            f"{prefix}_y_mm": _number(self.y_mm),
            f"{prefix}_z_mm": _number(self.z_mm),
            f"{prefix}_rx_rad": _number(self.rx_rad),
            f"{prefix}_ry_rad": _number(self.ry_rad),
            f"{prefix}_rz_rad": _number(self.rz_rad),
        }
        values.update(
            {
                f"{prefix}_j{index}_deg": _number(value)
                for index, value in enumerate(self.joints_deg, start=1)
            }
        )
        return values

    @classmethod
    def from_form(
        cls,
        values: Mapping[str, str],
        prefix: str,
    ) -> "PhotoPoseParameters":
        return cls(
            x_mm=_float(values, f"{prefix}_x_mm"),
            y_mm=_float(values, f"{prefix}_y_mm"),
            z_mm=_float(values, f"{prefix}_z_mm"),
            rx_rad=_float(values, f"{prefix}_rx_rad"),
            ry_rad=_float(values, f"{prefix}_ry_rad"),
            rz_rad=_float(values, f"{prefix}_rz_rad"),
            joints_deg=tuple(
                _float(values, f"{prefix}_j{index}_deg")
                for index in range(1, 7)
            ),
        )


BLOCK_PHOTO_POSE = PhotoPoseParameters(
    169.41,
    -394.49,
    428.00,
    -3.139,
    0.001,
    -0.277,
    (-35.89, 8.39, 109.55, 10.89, 90.0, 55.45),
)
TRAY_PHOTO_POSE = PhotoPoseParameters(
    -171.95,
    -334.41,
    428.02,
    -3.139,
    0.001,
    -0.276,
    (-83.90, 16.77, 115.91, 8.87, 89.87, 7.43),
)
TASK_CARD_PHOTO_POSE = PhotoPoseParameters(
    -209.36,
    -551.80,
    428.04,
    -3.139,
    0.001,
    -0.276,
    (-84.46, -14.81, 84.92, 9.45, 89.92, 6.87),
)

@dataclass(frozen=True, slots=True)
class RobotGlobalParameters:
    task_sequence: tuple[str, ...] = ("task1", "task2")
    step_action_sequence: tuple[str, ...] = DEFAULT_STEP_ACTIONS
    zero_joints_deg: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    block_photo_pose: PhotoPoseParameters = BLOCK_PHOTO_POSE
    tray_photo_pose: PhotoPoseParameters = TRAY_PHOTO_POSE
    task_card_photo_pose: PhotoPoseParameters = TASK_CARD_PHOTO_POSE
    tool_rx_deg: float = -180.0
    tool_ry_deg: float = 0.0
    vm_xy_scale_k: float = 1.0
    rz_sign: float = 1.0
    rz_offset_deg: float = 0.0
    base_plane_z_mm: float = 0.0
    block_pick_z_mm: float = 186.0
    block_place_z_mm: float = 182.0
    stack_z_mm: float = 208.3
    lift_distance_mm: float = 100.0
    suction_tcp_x_mm: float = -27.50
    suction_tcp_y_mm: float = -624.48
    camera_tcp_x_mm: float = -13.97
    camera_tcp_y_mm: float = -502.29
    workspace_min_x_mm: float = -886.5
    workspace_max_x_mm: float = 886.5
    workspace_min_y_mm: float = -886.5
    workspace_max_y_mm: float = 886.5
    workspace_min_z_mm: float = 0.0
    workspace_max_z_mm: float = 886.5
    workspace_radius_mm: float = 886.5
    joint_acceleration_rad_s2: float = radians(30.0)
    joint_velocity_rad_s: float = radians(60.0)
    transit_acceleration_m_s2: float = 0.4
    transit_velocity_m_s: float = 0.4
    precision_acceleration_m_s2: float = 0.3
    precision_velocity_m_s: float = 0.3
    completion_timeout_s: float = 60.0
    poll_interval_s: float = 0.10
    tool_do_index: int = 1
    tool_blow_do_index: int = 0
    tool_do_active_high: bool = True
    suction_settle_s: float = 0.20
    release_settle_s: float = 0.20
    photo_settle_s: float = 1.0
    model_result_settle_s: float = 1.0

    def __post_init__(self) -> None:
        if not self.task_sequence or set(self.task_sequence) - ALLOWED_TASKS:
            raise ValueError("任务顺序只能包含 task1 和 task2。")
        if len(set(self.task_sequence)) != len(self.task_sequence):
            raise ValueError("任务顺序不能重复。")
        action_set = set(self.step_action_sequence)
        if (
            len(action_set) != len(self.step_action_sequence)
            or not REQUIRED_STEP_ACTIONS.issubset(action_set)
            or action_set - ALLOWED_STEP_ACTIONS
        ):
            raise ValueError("每步必须各包含一次定位物块、定位托盘、抓取和放置，可选返回物块拍照位。")
        positions = {name: self.step_action_sequence.index(name) for name in action_set}
        if positions["locate_block"] > positions["pick"]:
            raise ValueError("必须先定位物块再抓取。")
        if positions["pick"] > positions["place"]:
            raise ValueError("必须先抓取再放置。")
        if positions["locate_tray"] > positions["place"]:
            raise ValueError("必须先定位托盘再放置。")
        if "return_photo" in positions and self.step_action_sequence[-1] != "return_photo":
            raise ValueError("启用返回物块拍照位时，该动作必须放在每步最后。")
        joint_poses = (
            self.zero_joints_deg,
            self.block_photo_pose.joints_deg,
            self.tray_photo_pose.joints_deg,
            self.task_card_photo_pose.joints_deg,
        )
        if any(len(pose) != 6 for pose in joint_poses):
            raise ValueError("零位和三个拍照位都必须包含六个关节角。")
        if not all(
            isfinite(value)
            for field in fields(self)
            for value in (getattr(self, field.name),)
            if isinstance(value, (float, int))
        ):
            raise ValueError("机械臂参数必须是有限数值。")
        if min(self.block_pick_z_mm, self.block_place_z_mm, self.stack_z_mm) < self.base_plane_z_mm:
            raise ValueError("抓取、放置和叠放高度不能低于基座平面。")
        if max(self.block_pick_z_mm, self.block_place_z_mm, self.stack_z_mm) + self.lift_distance_mm > self.workspace_max_z_mm:
            raise ValueError("相对抬升终点超出工作空间 Z 上限。")
        if not (
            self.workspace_min_x_mm < self.workspace_max_x_mm
            and self.workspace_min_y_mm < self.workspace_max_y_mm
            and self.workspace_min_z_mm < self.workspace_max_z_mm
        ):
            raise ValueError("工作空间最小值必须小于最大值。")
        positive = (
            self.workspace_radius_mm,
            self.vm_xy_scale_k,
            self.joint_acceleration_rad_s2,
            self.joint_velocity_rad_s,
            self.transit_acceleration_m_s2,
            self.transit_velocity_m_s,
            self.precision_acceleration_m_s2,
            self.precision_velocity_m_s,
            self.completion_timeout_s,
            self.poll_interval_s,
            self.lift_distance_mm,
        )
        if min(positive) <= 0:
            raise ValueError("工作空间、速度、加速度和超时参数必须大于0。")
        if not 0 <= self.tool_do_index <= 3 or not 0 <= self.tool_blow_do_index <= 3:
            raise ValueError("工具DO索引必须在0到3之间。")
        if self.tool_do_index == self.tool_blow_do_index:
            raise ValueError("吸气和吹气必须使用不同的工具DO。")
        if min(
            self.suction_settle_s,
            self.release_settle_s,
            self.photo_settle_s,
            self.model_result_settle_s,
        ) < 0:
            raise ValueError("吸盘和流程等待时间不能为负数。")

    def block_height_mm(self, color: Color) -> float:
        return self.block_pick_z_mm

    @property
    def tcp_delta_xy_mm(self) -> tuple[float, float]:
        return (self.suction_tcp_x_mm - self.camera_tcp_x_mm,
                self.suction_tcp_y_mm - self.camera_tcp_y_mm)

    @property
    def zero_joints_rad(self) -> tuple[float, ...]:
        return tuple(radians(value) for value in self.zero_joints_deg)

    @property
    def block_photo_joints_rad(self) -> tuple[float, ...]:
        return self.block_photo_pose.joints_rad

    @property
    def block_photo_joints_deg(self) -> tuple[float, ...]:
        return self.block_photo_pose.joints_deg

    @property
    def tray_photo_joints_rad(self) -> tuple[float, ...]:
        return self.tray_photo_pose.joints_rad

    @property
    def tray_photo_joints_deg(self) -> tuple[float, ...]:
        return self.tray_photo_pose.joints_deg

    @property
    def task_card_slot1_joints_rad(self) -> tuple[float, ...]:
        return self.task_card_photo_pose.joints_rad

    @property
    def task_card_slot1_joints_deg(self) -> tuple[float, ...]:
        return self.task_card_photo_pose.joints_deg

    @property
    def task_card_slot2_joints_rad(self) -> tuple[float, ...]:
        return self.task_card_photo_pose.joints_rad

    @property
    def task_card_slot2_joints_deg(self) -> tuple[float, ...]:
        return self.task_card_photo_pose.joints_deg

    def to_form_values(self) -> dict[str, str]:
        values = {
            field.name: str(getattr(self, field.name))
            for field in fields(self)
            for value in (getattr(self, field.name),)
            if not isinstance(value, (tuple, PhotoPoseParameters))
        }
        values["task_sequence"] = ",".join(self.task_sequence)
        values["step_action_sequence"] = ",".join(self.step_action_sequence)
        values["zero_joints_deg"] = ",".join(_number(value) for value in self.zero_joints_deg)
        for prefix, pose in (
            ("block_photo", self.block_photo_pose),
            ("tray_photo", self.tray_photo_pose),
            ("task_card_photo", self.task_card_photo_pose),
        ):
            values.update(pose.to_form_values(prefix))
        values["tool_do_active_high"] = "true" if self.tool_do_active_high else "false"
        return values

    @classmethod
    def from_form(cls, values: Mapping[str, str]) -> "RobotGlobalParameters":
        return cls(
            task_sequence=_words(values, "task_sequence"),
            step_action_sequence=_words(values, "step_action_sequence"),
            zero_joints_deg=_numbers(values, "zero_joints_deg", 6),
            block_photo_pose=PhotoPoseParameters.from_form(values, "block_photo"),
            tray_photo_pose=PhotoPoseParameters.from_form(values, "tray_photo"),
            task_card_photo_pose=PhotoPoseParameters.from_form(values, "task_card_photo"),
            tool_rx_deg=_float(values, "tool_rx_deg"),
            tool_ry_deg=_float(values, "tool_ry_deg"),
            vm_xy_scale_k=_float(values, "vm_xy_scale_k"),
            rz_sign=_float(values, "rz_sign"),
            rz_offset_deg=_float(values, "rz_offset_deg"),
            base_plane_z_mm=_float(values, "base_plane_z_mm"),
            block_pick_z_mm=_float(values, "block_pick_z_mm"),
            block_place_z_mm=_float(values, "block_place_z_mm"),
            stack_z_mm=_float(values, "stack_z_mm"),
            lift_distance_mm=_float(values, "lift_distance_mm"),
            suction_tcp_x_mm=_float(values, "suction_tcp_x_mm"),
            suction_tcp_y_mm=_float(values, "suction_tcp_y_mm"),
            camera_tcp_x_mm=_float(values, "camera_tcp_x_mm"),
            camera_tcp_y_mm=_float(values, "camera_tcp_y_mm"),
            workspace_min_x_mm=_float(values, "workspace_min_x_mm"),
            workspace_max_x_mm=_float(values, "workspace_max_x_mm"),
            workspace_min_y_mm=_float(values, "workspace_min_y_mm"),
            workspace_max_y_mm=_float(values, "workspace_max_y_mm"),
            workspace_min_z_mm=_float(values, "workspace_min_z_mm"),
            workspace_max_z_mm=_float(values, "workspace_max_z_mm"),
            workspace_radius_mm=_float(values, "workspace_radius_mm"),
            joint_acceleration_rad_s2=_float(values, "joint_acceleration_rad_s2"),
            joint_velocity_rad_s=_float(values, "joint_velocity_rad_s"),
            transit_acceleration_m_s2=_float(values, "transit_acceleration_m_s2"),
            transit_velocity_m_s=_float(values, "transit_velocity_m_s"),
            precision_acceleration_m_s2=_float(values, "precision_acceleration_m_s2"),
            precision_velocity_m_s=_float(values, "precision_velocity_m_s"),
            completion_timeout_s=_float(values, "completion_timeout_s"),
            poll_interval_s=_float(values, "poll_interval_s"),
            tool_do_index=_int(values, "tool_do_index"),
            tool_blow_do_index=_int(values, "tool_blow_do_index"),
            tool_do_active_high=_bool(values, "tool_do_active_high"),
            suction_settle_s=_float(values, "suction_settle_s"),
            release_settle_s=_float(values, "release_settle_s"),
            photo_settle_s=_float(values, "photo_settle_s"),
            model_result_settle_s=_float(values, "model_result_settle_s"),
        )


ROBOT_GLOBAL_PARAMETERS = RobotGlobalParameters()


class RobotParameterStore:
    def __init__(
        self,
        parameters: RobotGlobalParameters = ROBOT_GLOBAL_PARAMETERS,
    ) -> None:
        self._parameters = parameters
        self._lock = Lock()

    def snapshot(self) -> RobotGlobalParameters:
        with self._lock:
            return self._parameters

    def form_values(self) -> dict[str, str]:
        return self.snapshot().to_form_values()

    def update_from_form(
        self, overrides: Mapping[str, str]
    ) -> RobotGlobalParameters:
        with self._lock:
            merged = self._parameters.to_form_values()
            merged.update(overrides)
            updated = RobotGlobalParameters.from_form(merged)
            self._parameters = updated
            return updated


def _raw(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"机械臂参数 {name} 不能为空。")
    return value


def _float(values: Mapping[str, str], name: str) -> float:
    try:
        return float(_raw(values, name))
    except ValueError as exc:
        raise ValueError(f"机械臂参数 {name} 必须是数字。") from exc


def _int(values: Mapping[str, str], name: str) -> int:
    try:
        return int(_raw(values, name))
    except ValueError as exc:
        raise ValueError(f"机械臂参数 {name} 必须是整数。") from exc


def _bool(values: Mapping[str, str], name: str) -> bool:
    value = _raw(values, name).lower()
    if value in {"true", "1", "yes", "是"}:
        return True
    if value in {"false", "0", "no", "否"}:
        return False
    raise ValueError(f"机械臂参数 {name} 必须是 true 或 false。")


def _words(values: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _raw(values, name).split(",") if part.strip())


def _numbers(values: Mapping[str, str], name: str, count: int) -> tuple[float, ...]:
    try:
        parsed = tuple(float(part.strip()) for part in _raw(values, name).split(","))
    except ValueError as exc:
        raise ValueError(f"机械臂参数 {name} 必须是逗号分隔的数字。") from exc
    if len(parsed) != count:
        raise ValueError(f"机械臂参数 {name} 必须包含{count}个数字。")
    return parsed


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
