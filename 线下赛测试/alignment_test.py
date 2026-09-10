from __future__ import annotations

import json
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from math import radians, sqrt, isfinite
from threading import Event, Lock
from typing import Callable, Protocol

_RESPONSE_PATTERN = re.compile(
    r"^#0;([-+]?\d+(?:\.\d+)?);([-+]?\d+(?:\.\d+)?);([-+]?\d+(?:\.\d+)?);$"
)


@dataclass(frozen=True, slots=True)
class ArcsMotionSafetyStatus:
    powered_on: bool
    steady: bool
    within_safety_limits: bool
    collision_occurred: bool


class TcpVmClient:
    def __init__(self, host: str, port: int, timeout_s: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._timeout_s = timeout_s

    def request_payload(self, request: str) -> bytes:
        normalized = request.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise ValueError("VM 请求必须是一行纯文本。")
        chunks: list[bytes] = []
        try:
            connection = socket.create_connection(
                (self._host, self._port),
                timeout=self._timeout_s,
            )
            with connection:
                connection.settimeout(self._timeout_s)
                connection.sendall(normalized.encode("utf-8"))
                while True:
                    try:
                        chunk = connection.recv(4096)
                    except socket.timeout:
                        if chunks:
                            break
                        raise TimeoutError("等待 VM 坐标响应超时。") from None
                    if not chunk:
                        break
                    chunks.append(chunk)
        except OSError as exc:
            raise ConnectionError(
                f"VM TCP 通信失败：{self._host}:{self._port}。"
            ) from exc
        payload = b"".join(chunks)
        if not payload:
            raise ValueError("VM 未返回坐标数据。")
        return payload


class AuboArcsClient:
    def __init__(
        self,
        host: str,
        port: int,
        robot_name: str,
        timeout_s: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._robot_name = robot_name
        self._timeout_s = timeout_s
        self._next_id = 1

    def get_motion_safety_status(self) -> ArcsMotionSafetyStatus:
        prefix = f"{self._robot_name}.RobotState."
        return ArcsMotionSafetyStatus(
            self._boolean(self._call(f"{prefix}isPowerOn", []), "上电状态"),
            self._boolean(self._call(f"{prefix}isSteady", []), "运动停止状态"),
            self._boolean(
                self._call(f"{prefix}isWithinSafetyLimits", []),
                "安全限位状态",
            ),
            self._boolean(
                self._call(f"{prefix}isCollisionOccurred", []),
                "碰撞状态",
            ),
        )

    def get_tcp_pose(self) -> tuple[float, float, float, float, float, float]:
        value = self._call(
            f"{self._robot_name}.RobotState.getTcpPose",
            [],
        )
        if not isinstance(value, list) or len(value) != 6:
            raise ValueError("ARCS 工具位姿必须包含6个数值。")
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        ):
            raise ValueError("ARCS 工具位姿包含非数值。")
        return tuple(float(item) for item in value)

    def move_line(
        self,
        pose_m_rad,
        *,
        acceleration_m_s2: float,
        velocity_m_s: float,
    ) -> int:
        result = self._call(
            f"{self._robot_name}.MotionControl.moveLine",
            [list(pose_m_rad), acceleration_m_s2, velocity_m_s, 0.0, 0.0],
        )
        if isinstance(result, bool) or not isinstance(result, int):
            raise ValueError("ARCS moveLine 未返回整数状态码。")
        if result not in {0, 13}:
            raise RuntimeError(f"ARCS moveLine 返回失败状态码 {result}。")
        return result

    def wait_until_steady(self, *, timeout_s: float, poll_interval_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_motion_safety_status()
            if status.collision_occurred:
                self.emergency_stop()
                raise RuntimeError("运动中检测到碰撞，已紧急停止。")
            if status.steady:
                return
            if time.monotonic() >= deadline:
                self.emergency_stop()
                raise TimeoutError("等待机械臂停止超时，已紧急停止。")
            time.sleep(poll_interval_s)

    def set_tool_digital_output(self, index: int, value: bool) -> int:
        result = self._call(f"{self._robot_name}.IoControl.setToolDigitalOutput", [index, value])
        if type(result) is not int or result != 0:
            raise RuntimeError(f"ARCS 工具 DO 设置失败：{result}")
        return result

    def emergency_stop(self) -> int:
        result = self._call(
            f"{self._robot_name}.MotionControl.stopMove",
            [True, True],
        )
        if isinstance(result, bool) or not isinstance(result, int):
            raise ValueError("ARCS stopMove 未返回整数状态码。")
        return result

    def _call(self, method: str, params: list[object]) -> object:
        request_id = self._next_id
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }
        try:
            connection = socket.create_connection(
                (self._host, self._port),
                timeout=self._timeout_s,
            )
            with connection:
                connection.settimeout(self._timeout_s)
                connection.sendall(
                    json.dumps(request, ensure_ascii=False).encode("utf-8")
                )
                response = self._receive_json(connection)
        except OSError as exc:
            raise ConnectionError(
                f"ARCS JSON-RPC 通信失败：{self._host}:{self._port}。"
            ) from exc
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise ValueError("ARCS JSON-RPC 响应版本或请求 ID 不匹配。")
        if "error" in response:
            error = response["error"]
            message = error.get("message", "未知错误") if isinstance(error, Mapping) else "未知错误"
            raise RuntimeError(f"ARCS 拒绝 {method}：{message}")
        if "result" not in response:
            raise ValueError("ARCS JSON-RPC 响应缺少 result。")
        return response["result"]

    @staticmethod
    def _receive_json(connection: socket.socket) -> Mapping[str, object]:
        decoder = json.JSONDecoder()
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            try:
                response, _ = decoder.raw_decode(
                    b"".join(chunks).decode("utf-8").lstrip()
                )
            except json.JSONDecodeError:
                continue
            if not isinstance(response, dict):
                raise ValueError("ARCS JSON-RPC 响应顶层必须是对象。")
            return response
        raise ValueError("ARCS 在返回 JSON-RPC 响应前关闭了连接。")

    @staticmethod
    def _boolean(value: object, name: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"ARCS {name} 必须是布尔值。")
        return value


@dataclass(frozen=True, slots=True)
class AlignmentMeasurement:
    x_mm: float
    y_mm: float
    rz_deg: float
    raw_packet: str


@dataclass(frozen=True, slots=True)
class AlignmentTestConfig:
    vm_host: str = "127.0.0.1"
    vm_port: int = 7930
    arcs_host: str = "192.168.1.12"
    arcs_port: int = 30004
    robot_name: str = "rob1"
    trigger: int = 11
    suction_tcp_x_mm: float = -27.50
    suction_tcp_y_mm: float = -624.48
    camera_tcp_x_mm: float = -13.97
    camera_tcp_y_mm: float = -502.29
    block_pick_z_mm: float = 186.0
    block_place_z_mm: float = 182.0
    stack_z_mm: float = 208.3
    lift_distance_mm: float = 100.0
    tool_do_index: int = 1
    tool_do_active_high: bool = True
    suction_settle_s: float = 0.2
    release_settle_s: float = 0.2
    vm_xy_scale_k: float = 1.0
    rz_sign: float = 1.0
    rz_offset_deg: float = 0.0
    acceleration_m_s2: float = 0.4
    velocity_m_s: float = 0.4
    precision_acceleration_m_s2: float = 0.3
    precision_velocity_m_s: float = 0.3
    motion_timeout_s: float = 60.0
    poll_interval_s: float = 0.1
    max_planar_radius_mm: float = 886.5

    def __post_init__(self) -> None:
        if not self.vm_host.strip() or not self.arcs_host.strip():
            raise ValueError("VM 与 ARCS IP 不能为空。")
        if not 1 <= self.vm_port <= 65535 or not 1 <= self.arcs_port <= 65535:
            raise ValueError("VM 与 ARCS 端口必须在 1 到 65535 之间。")
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, (int, float)) and not isfinite(value):
                raise ValueError("测试参数必须是有限数值。")
        measurement_request(self.trigger)
        if not 0 <= self.tool_do_index <= 3:
            raise ValueError("工具 DO 索引必须在0–3之间。")
        if min(self.block_pick_z_mm, self.block_place_z_mm, self.stack_z_mm) < 0 or self.lift_distance_mm <= 0:
            raise ValueError("抓取/放置高度不能为负数，抬升距离必须大于零。")
        if min(self.suction_settle_s, self.release_settle_s) < 0:
            raise ValueError("吸盘等待时间不能为负数。")
        if min(self.precision_acceleration_m_s2, self.precision_velocity_m_s, self.max_planar_radius_mm) <= 0:
            raise ValueError("慢速运动参数和工作半径必须大于零。")
        if self.vm_xy_scale_k <= 0:
            raise ValueError("VM X/Y 偏移比例 k 必须大于 0。")
        if self.acceleration_m_s2 <= 0 or self.velocity_m_s <= 0:
            raise ValueError("速度与加速度必须大于 0。")
        if self.motion_timeout_s <= 0 or self.poll_interval_s <= 0:
            raise ValueError("运动超时与轮询间隔必须大于 0。")


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    measurement: AlignmentMeasurement
    target_pose_m_rad: tuple[float, float, float, float, float, float]
    motion_status: int


class VmClientPort(Protocol):
    def request_payload(self, request: str) -> bytes: ...


class ArcsClientPort(Protocol):
    def get_motion_safety_status(self): ...

    def get_tcp_pose(self): ...

    def move_line(
        self,
        pose_m_rad,
        *,
        acceleration_m_s2: float,
        velocity_m_s: float,
    ) -> int: ...

    def wait_until_steady(self, *, timeout_s: float, poll_interval_s: float) -> None: ...


def parse_alignment_response(payload: bytes) -> AlignmentMeasurement:
    try:
        text = payload.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("VM 响应不是 UTF-8 文本。") from exc
    if text.startswith(("b'", 'b"')):
        raise ValueError("VM 响应包含了字节字面量，不是纯文本。")
    match = _RESPONSE_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError("VM 必须返回一条 #0;X;Y;RZ; 格式的数据。")
    x_mm, y_mm, rz_deg = (float(value) for value in match.groups())
    return AlignmentMeasurement(x_mm, y_mm, rz_deg, text)


def build_camera_target(
    measurement: AlignmentMeasurement,
    current_pose_m_rad: tuple[float, float, float, float, float, float],
    config: AlignmentTestConfig,
) -> tuple[float, float, float, float, float, float]:
    target = (
        current_pose_m_rad[0]
        + config.vm_xy_scale_k * measurement.x_mm / 1000.0,
        current_pose_m_rad[1]
        + config.vm_xy_scale_k * measurement.y_mm / 1000.0,
        current_pose_m_rad[2],
        current_pose_m_rad[3],
        current_pose_m_rad[4],
        current_pose_m_rad[5]
        + radians(config.rz_offset_deg + config.rz_sign * measurement.rz_deg),
    )
    if sqrt(target[0] ** 2 + target[1] ** 2) * 1000.0 > config.max_planar_radius_mm:
        raise ValueError("计算后的目标 X/Y 超出 AUBO ES5 最大平面臂展。")
    return target


def measurement_request(trigger: int) -> str:
    if trigger in range(11, 17):
        return f"wukuai,{trigger}"
    if trigger in range(21, 27):
        return f"tuopan,{trigger}"
    raise ValueError("编号只允许物块11–16或托盘21–26。")


def build_suction_target(camera, config):
    target = (camera[0] + (config.suction_tcp_x_mm - config.camera_tcp_x_mm) / 1000,
              camera[1] + (config.suction_tcp_y_mm - config.camera_tcp_y_mm) / 1000,
              *camera[2:])
    require_workspace(target, config)
    return target


def require_workspace(target, config):
    if not all(isfinite(value) for value in target):
        raise ValueError("目标 TCP 必须是有限数值。")
    if not 0 <= target[2] * 1000 <= config.max_planar_radius_mm:
        raise ValueError("目标高度超出工作空间。")
    if sqrt(target[0] ** 2 + target[1] ** 2) * 1000 > config.max_planar_radius_mm:
        raise ValueError("目标 X/Y 超出 AUBO ES5 最大平面臂展。")


class CameraAlignmentTest:
    def __init__(
        self,
        config: AlignmentTestConfig,
        *,
        vm_client: VmClientPort | None = None,
        arcs_client: ArcsClientPort | None = None,
        report: Callable[[str], None] = print,
    ) -> None:
        self._config = config
        self._vm = vm_client or TcpVmClient(config.vm_host, config.vm_port)
        self._arcs = arcs_client or AuboArcsClient(
            config.arcs_host,
            config.arcs_port,
            config.robot_name,
        )
        self._report = report
        self.cancelled = Event()
        self._command_lock = Lock()

    def run(self, action: str = "align") -> AlignmentResult:
        if action not in {"align", "pick", "place"}:
            raise ValueError("测试动作只能为 align、pick 或 place。")
        if action == "pick" and self._config.trigger not in range(11, 17):
            raise ValueError("抓取测试必须选择物块编号11–16。")
        if action == "place" and self._config.trigger not in range(21, 27):
            raise ValueError("放置测试必须选择托盘编号21–26。")
        self._check()
        request = measurement_request(self._config.trigger)
        self._report(f"向 VisionMaster 发送：{request}")
        measurement = parse_alignment_response(self._vm.request_payload(request))
        self._check()
        self._report(
            f"收到坐标：X={measurement.x_mm:.3f} mm，"
            f"Y={measurement.y_mm:.3f} mm，RZ={measurement.rz_deg:.3f}°"
        )
        safety = self._arcs.get_motion_safety_status()
        if not safety.powered_on:
            raise RuntimeError("机械臂尚未上电，已阻止运动。")
        if not safety.steady:
            raise RuntimeError("机械臂仍在运动，已阻止新指令。")
        if not safety.within_safety_limits or safety.collision_occurred:
            raise RuntimeError("机械臂安全状态异常，已阻止运动。")
        current_pose = self._arcs.get_tcp_pose()
        self._report(
            "当前 TCP 原点："
            f"X={current_pose[0] * 1000:.3f} mm，"
            f"Y={current_pose[1] * 1000:.3f} mm，"
            f"RZ={current_pose[5]:.4f} rad；k={self._config.vm_xy_scale_k:g}"
        )
        target = build_camera_target(measurement, current_pose, self._config)
        self._report(
            "目标 TCP："
            f"X={target[0] * 1000:.3f} mm，Y={target[1] * 1000:.3f} mm，"
            f"Z={target[2] * 1000:.3f} mm，RX={target[3]:.4f} rad，"
            f"RY={target[4]:.4f} rad，RZ={target[5]:.4f} rad"
        )
        cup = build_suction_target(target, self._config)
        require_workspace(target, self._config)
        height = self._config.block_pick_z_mm if action == "pick" else self._config.block_place_z_mm
        if action != "align":
            if target[2] * 1000 <= height:
                raise ValueError("当前拍照高度必须高于抓取/放置高度。")
            down = (*cup[:2], height / 1000, *cup[3:])
            up = (*cup[:2], (height + self._config.lift_distance_mm) / 1000, *cup[3:])
            require_workspace(down, self._config)
            require_workspace(up, self._config)
        try:
            self._move(target)
            self._report("相机对准已完成。")
            status = self._move(cup)
            self._report("TCP-Delta 对准已完成；未对 Z 叠加偏移。")
            if action != "align":
                self._move(down, precision=True)
                with self._command_lock:
                    self._check()
                    enabled = action == "pick"
                    value = self._config.tool_do_active_high if enabled else not self._config.tool_do_active_high
                    self._arcs.set_tool_digital_output(self._config.tool_do_index, value)
                self.cancelled.wait(self._config.suction_settle_s if enabled else self._config.release_settle_s)
                self._check()
                self._move(up)
                self._report("抓取/放置和相对抬升已完成。")
            return AlignmentResult(measurement, cup, status)
        except Exception:
            self._arcs.emergency_stop()
            raise

    def _check(self):
        if self.cancelled.is_set():
            raise RuntimeError("测试已重置，拒绝继续运动。")

    def _move(self, target, precision=False):
        with self._command_lock:
            self._check()
            safety = self._arcs.get_motion_safety_status()
            if not safety.powered_on or not safety.within_safety_limits or safety.collision_occurred:
                raise RuntimeError("安全状态异常，拒绝下发运动。")
            self._check()
            status = self._arcs.move_line(
                list(target),
                acceleration_m_s2=self._config.precision_acceleration_m_s2 if precision else self._config.acceleration_m_s2,
                velocity_m_s=self._config.precision_velocity_m_s if precision else self._config.velocity_m_s,
            )
        self._arcs.wait_until_steady(timeout_s=self._config.motion_timeout_s, poll_interval_s=self._config.poll_interval_s)
        self._check()
        return status

    def reset(self):
        self.cancelled.set()
        with self._command_lock:
            self._arcs.emergency_stop()
            self._arcs.wait_until_steady(timeout_s=10, poll_interval_s=0.1)
