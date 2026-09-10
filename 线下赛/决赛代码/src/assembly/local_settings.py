from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .robot_parameters import RobotGlobalParameters


@dataclass(frozen=True, slots=True)
class RememberedConnection:
    visionmaster_host: str
    visionmaster_port: int
    arcs_host: str
    arcs_port: int
    task_card_image_directory: str = "C:/VisionMaster/task_cards"

    def __post_init__(self) -> None:
        if not self.visionmaster_host.strip() or not self.arcs_host.strip():
            raise ValueError("记忆的IP地址不能为空。")
        if not 1 <= self.visionmaster_port <= 65535:
            raise ValueError("记忆的VisionMaster端口无效。")
        if not 1 <= self.arcs_port <= 65535:
            raise ValueError("记忆的ARCS端口无效。")
        if not self.task_card_image_directory.strip():
            raise ValueError("记忆的任务卡图片文件夹不能为空。")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "visionmaster_host": self.visionmaster_host,
            "visionmaster_port": self.visionmaster_port,
            "arcs_host": self.arcs_host,
            "arcs_port": self.arcs_port,
            "task_card_image_directory": self.task_card_image_directory,
        }

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "RememberedConnection":
        return cls(
            str(values["visionmaster_host"]).strip(),
            int(values["visionmaster_port"]),
            str(values["arcs_host"]).strip(),
            int(values["arcs_port"]),
            str(values["task_card_image_directory"]).strip(),
        )


@dataclass(frozen=True, slots=True)
class RememberedLocalSettings:
    connection: RememberedConnection
    robot_parameters: RobotGlobalParameters


class LocalSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(
        self,
        default_connection: RememberedConnection,
        default_robot_parameters: RobotGlobalParameters,
    ) -> RememberedLocalSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            connection_raw = raw["connection"]
            robot_raw = raw["robot_parameters"]
            if not isinstance(connection_raw, dict) or not isinstance(robot_raw, dict):
                raise ValueError
            merged_connection = default_connection.to_dict()
            merged_connection.update(connection_raw)
            connection = RememberedConnection.from_mapping(merged_connection)
            merged_robot = default_robot_parameters.to_form_values()
            robot_values = {str(key): str(value) for key, value in robot_raw.items()}
            if raw.get("schema_version", 1) < 2:
                for name in (
                    "joint_acceleration_rad_s2", "joint_velocity_rad_s",
                    "transit_acceleration_m_s2", "transit_velocity_m_s",
                    "precision_acceleration_m_s2", "precision_velocity_m_s",
                    "step_action_sequence",
                ):
                    robot_values.pop(name, None)
                for name in list(merged_robot):
                    if name.startswith("task_card_photo_"):
                        legacy = name.replace("task_card_photo_", "task_card_slot1_photo_")
                        if legacy in robot_values:
                            robot_values[name] = robot_values[legacy]
            merged_robot.update({key: value for key, value in robot_values.items() if key in merged_robot})
            robot_parameters = RobotGlobalParameters.from_form(merged_robot)
            return RememberedLocalSettings(connection, robot_parameters)
        except (FileNotFoundError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return RememberedLocalSettings(
                default_connection,
                default_robot_parameters,
            )

    def save(
        self,
        connection: RememberedConnection,
        robot_parameters: RobotGlobalParameters,
    ) -> None:
        payload = {
            "schema_version": 2,
            "connection": connection.to_dict(),
            "robot_parameters": robot_parameters.to_form_values(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
