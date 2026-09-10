"Tested JSON-RPC transport for the ARCS controller's read-only safety checks."

from __future__ import annotations

import json
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .settings import AuboArcsConnectionConfig


class ArcsConnectionError(ConnectionError):
    'Raised when the ARCS JSON-RPC service cannot be reached.'
    pass


class ArcsProtocolError(ValueError):
    'Raised when ARCS returns an invalid JSON-RPC response.'
    pass


class ArcsRemoteError(RuntimeError):
    'Raised when ARCS rejects a correctly formatted JSON-RPC request.'
    pass


class JsonRpcTransportPort(Protocol):
    def request(
        self,
        host: str,
        port: int,
        request: Mapping[str, object],
        timeout_s: float,
    ) -> Mapping[str, object]: ...


class TcpJsonRpcTransport:

    'One-request-per-connection TCP JSON-RPC transport for ARCS port 30004.'
    def request(
        self,
        host: str,
        port: int,
        request: Mapping[str, object],
        timeout_s: float,
    ) -> Mapping[str, object]:
        try:
            connection = socket.create_connection((host, port), timeout=timeout_s)
        except OSError as exc:
            raise ArcsConnectionError(
                f"无法连接 ARCS JSON-RPC 服务 {host}:{port}。"
            ) from exc

        encoded_request = json.dumps(request, ensure_ascii=False).encode("utf-8")
        decoder = json.JSONDecoder()
        chunks: list[bytes] = []
        with connection:
            connection.settimeout(timeout_s)
            try:
                connection.sendall(encoded_request)
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    response_text = b"".join(chunks).decode("utf-8")
                    try:
                        response, _ = decoder.raw_decode(response_text.lstrip())
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(response, dict):
                        raise ArcsProtocolError(
                            "ARCS JSON-RPC 响应顶层必须是对象。"
                        )
                    return response
            except socket.timeout as exc:
                raise ArcsConnectionError("等待 ARCS JSON-RPC 响应超时。") from exc
            except OSError as exc:
                raise ArcsConnectionError("ARCS JSON-RPC 通信失败。") from exc

        raise ArcsProtocolError("ARCS 在返回 JSON-RPC 响应前关闭了连接。")


@dataclass(frozen=True, slots=True)
class ArcsRobotSnapshot:

    'Read-only state required to verify an ARCS simulation before any motion.'
    robot_name: str
    joint_positions_rad: tuple[float, float, float, float, float, float]
    tool_pose_m_rad: tuple[float, float, float, float, float, float]
    joint_states: tuple[str, str, str, str, str, str]
    collision_occurred: bool


@dataclass(frozen=True, slots=True)
class ArcsMotionSafetyStatus:

    'Safety state that must be healthy before a virtual motion is queued.'
    powered_on: bool
    steady: bool
    within_safety_limits: bool
    collision_occurred: bool


@dataclass(frozen=True, slots=True)
class ArcsTraceRecord:
    timestamp: int
    level: str
    source: str
    code: int
    args: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.args)


class AuboArcsJsonRpcClient:

    'ARCS controller client; motion commands are intentionally not exposed yet.'
    def __init__(
        self,
        config: AuboArcsConnectionConfig,
        *,
        transport: JsonRpcTransportPort | None = None,
    ) -> None:
        self._config = config
        self._transport = TcpJsonRpcTransport() if transport is None else transport
        self._next_id = 1

    def get_robot_names(self) -> tuple[str, ...]:
        result = self._call("getRobotNames", [])
        if not isinstance(result, list) or not all(
            isinstance(name, str) and name for name in result
        ):
            raise ArcsProtocolError("ARCS getRobotNames 返回了无效结果。")
        return tuple(result)

    def get_snapshot(self) -> ArcsRobotSnapshot:
        robot_name = self._config.robot_name
        if robot_name not in self.get_robot_names():
            raise ArcsProtocolError(
                f"ARCS 未返回配置的机器人 {robot_name}。"
            )
        prefix = f"{robot_name}.RobotState."
        return ArcsRobotSnapshot(
            robot_name=robot_name,
            joint_positions_rad=_six_numbers(
                self._call(f"{prefix}getJointPositions", []),
                "关节位置",
            ),
            tool_pose_m_rad=self.get_tcp_pose(),
            joint_states=_six_strings(
                self._call(f"{prefix}getJointState", []),
                "关节状态",
            ),
            collision_occurred=_bool(
                self._call(f"{prefix}isCollisionOccurred", []),
                "碰撞状态",
            ),
        )

    def get_tool_pose(self) -> tuple[float, float, float, float, float, float]:
        return _six_numbers(
            self._call(
                f"{self._config.robot_name}.RobotState.getToolPose",
                [],
            ),
            "工具位姿",
        )

    def get_tcp_pose(self) -> tuple[float, float, float, float, float, float]:
        return _six_numbers(
            self._call(f"{self._config.robot_name}.RobotState.getTcpPose", []),
            "TCP 位姿",
        )

    def get_motion_safety_status(self) -> ArcsMotionSafetyStatus:

        'Read the motion preconditions without changing robot state.'
        prefix = f"{self._config.robot_name}.RobotState."
        return ArcsMotionSafetyStatus(
            powered_on=_bool(self._call(f"{prefix}isPowerOn", []), "上电状态"),
            steady=_bool(self._call(f"{prefix}isSteady", []), "运动停止状态"),
            within_safety_limits=_bool(
                self._call(f"{prefix}isWithinSafetyLimits", []), "安全限位状态"
            ),
            collision_occurred=_bool(
                self._call(f"{prefix}isCollisionOccurred", []), "碰撞状态"
            ),
        )

    def move_joint(
        self,
        joints_rad: tuple[float, float, float, float, float, float],
        *,
        acceleration_rad_s2: float,
        velocity_rad_s: float,
    ) -> int:

        'Queue one documented ARCS ``MotionControl.moveJoint`` command.'
        _positive_number(acceleration_rad_s2, "关节加速度")
        _positive_number(velocity_rad_s, "关节速度")
        return _motion_status_code(
            self._call(
                f"{self._config.robot_name}.MotionControl.moveJoint",
                [list(joints_rad), acceleration_rad_s2, velocity_rad_s, 0.0, 0.0],
            ),
            "moveJoint",
        )

    def move_line(
        self,
        pose_m_rad: list[float],
        *,
        acceleration_m_s2: float,
        velocity_m_s: float,
    ) -> int:

        'Queue one documented ARCS ``MotionControl.moveLine`` command.'
        if len(pose_m_rad) != 6 or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in pose_m_rad
        ):
            raise ValueError("直线运动目标必须是六个数值。")
        _positive_number(acceleration_m_s2, "直线加速度")
        _positive_number(velocity_m_s, "直线速度")
        return _motion_status_code(
            self._call(
                f"{self._config.robot_name}.MotionControl.moveLine",
                [pose_m_rad, acceleration_m_s2, velocity_m_s, 0.0, 0.0],
            ),
            "moveLine",
        )

    def set_tool_digital_output(self, index: int, value: bool) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 3:
            raise ValueError("工具数字输出索引必须在0到3之间。")
        if not isinstance(value, bool):
            raise ValueError("工具数字输出值必须是布尔值。")
        return _status_code(
            self._call(
                f"{self._config.robot_name}.IoControl.setToolDigitalOutput",
                [index, value],
            ),
            "setToolDigitalOutput",
        )

    def trace_textmsg(self, message: str) -> int:

        'Write an evidence message into the ARCS native home-page log.'
        if not message.strip():
            raise ValueError("ARCS 日志内容不能为空。")
        return _status_code(
            self._call(
                f"{self._config.robot_name}.Trace.textmsg",
                [message],
            ),
            "Trace.textmsg",
        )

    def peek_trace(
        self, *, max_records: int = 10000, last_time: int = 0
    ) -> tuple[ArcsTraceRecord, ...]:
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records <= 0:
            raise ValueError("ARCS日志读取数量必须为正整数。")
        if isinstance(last_time, bool) or not isinstance(last_time, int) or last_time < 0:
            raise ValueError("ARCS日志起始时间戳必须为非负整数。")
        result = self._call(
            f"{self._config.robot_name}.Trace.peek",
            [max_records, last_time],
        )
        if not isinstance(result, list):
            raise ArcsProtocolError("ARCS Trace.peek 返回结果必须是数组。")
        records: list[ArcsTraceRecord] = []
        for index, item in enumerate(result, start=1):
            if not isinstance(item, Mapping):
                raise ArcsProtocolError(f"ARCS Trace.peek 第{index}条记录不是对象。")
            timestamp = item.get("timestamp")
            level = item.get("level")
            source = item.get("source")
            code = item.get("code")
            args = item.get("args")
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise ArcsProtocolError(f"ARCS Trace.peek 第{index}条时间戳无效。")
            if not isinstance(level, str) or not level:
                raise ArcsProtocolError(f"ARCS Trace.peek 第{index}条日志级别无效。")
            if not isinstance(source, str) or not source:
                raise ArcsProtocolError(f"ARCS Trace.peek 第{index}条日志来源无效。")
            if isinstance(code, bool) or not isinstance(code, int):
                raise ArcsProtocolError(f"ARCS Trace.peek 第{index}条错误码无效。")
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ArcsProtocolError(f"ARCS Trace.peek 第{index}条日志参数无效。")
            records.append(
                ArcsTraceRecord(timestamp, level, source, code, tuple(args))
            )
        return tuple(records)

    def wait_until_steady(
        self,
        *,
        timeout_s: float,
        poll_interval_s: float,
    ) -> None:

        'Wait for queued motion to finish and stop immediately on collision.'
        _positive_number(timeout_s, "运动等待超时")
        _positive_number(poll_interval_s, "运动轮询间隔")
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_motion_safety_status()
            if status.collision_occurred:
                self.emergency_stop()
                raise ArcsRemoteError("ARCS 仿真运动中检测到碰撞，已发送紧急停止。")
            if status.steady:
                return
            if time.monotonic() >= deadline:
                self.emergency_stop()
                raise ArcsConnectionError("等待 ARCS 仿真机械臂停止超时，已发送紧急停止。")
            time.sleep(poll_interval_s)

    def emergency_stop(self) -> int:

        'Send ARCS stopMove(quick=True, all_tasks=True) only on a real emergency.'
        result = self._call(
            f"{self._config.robot_name}.MotionControl.stopMove",
            [True, True],
        )
        if isinstance(result, bool) or not isinstance(result, int):
            raise ArcsProtocolError("ARCS stopMove 未返回整数状态码。")
        return result

    def _call(self, method: str, params: list[object]) -> object:
        request_id = self._next_id
        self._next_id += 1
        response = self._transport.request(
            self._config.host,
            self._config.port,
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": request_id,
            },
            self._config.request_timeout_s,
        )
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise ArcsProtocolError("ARCS JSON-RPC 响应的版本或请求 ID 不匹配。")
        if "error" in response:
            error = response["error"]
            if isinstance(error, Mapping):
                message = error.get("message", "未知错误")
            else:
                message = "未知错误"
            raise ArcsRemoteError(f"ARCS 拒绝 {method}：{message}")
        if "result" not in response:
            raise ArcsProtocolError("ARCS JSON-RPC 响应缺少 result。")
        return response["result"]


def _six_numbers(
    value: object, field_name: str
) -> tuple[float, float, float, float, float, float]:
    if not isinstance(value, list) or len(value) != 6:
        raise ArcsProtocolError(f"ARCS {field_name} 必须是 6 个数值。")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ArcsProtocolError(f"ARCS {field_name} 包含非数值。")
    return tuple(float(item) for item in value)  


def _six_strings(
    value: object, field_name: str
) -> tuple[str, str, str, str, str, str]:
    if not isinstance(value, list) or len(value) != 6:
        raise ArcsProtocolError(f"ARCS {field_name} 必须是 6 个字符串。")
    if any(not isinstance(item, str) or not item for item in value):
        raise ArcsProtocolError(f"ARCS {field_name} 包含无效字符串。")
    return tuple(value)  


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ArcsProtocolError(f"ARCS {field_name} 必须是布尔值。")
    return value


def _status_code(value: object, method_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArcsProtocolError(f"ARCS {method_name} 未返回整数状态码。")
    if value != 0:
        raise ArcsRemoteError(f"ARCS {method_name} 返回失败状态码 {value}。")
    return value


def _motion_status_code(value: object, method_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArcsProtocolError(f"ARCS {method_name} 未返回整数状态码。")
    if value not in {0, 13}:
        raise ArcsRemoteError(f"ARCS {method_name} 返回失败状态码 {value}。")
    return value


def _positive_number(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field_name}必须大于 0。")
