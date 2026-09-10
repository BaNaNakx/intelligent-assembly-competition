'Confirmed offline-competition device profile.'

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfflineDeviceProfile:
    robot_model: str
    edge_controller_model: str
    camera_connection: str


OFFLINE_DEVICE_PROFILE = OfflineDeviceProfile(
    robot_model="AUBO ES5",
    edge_controller_model="AUBO ERC",
    camera_connection="VisionMaster TCP/IP",
)
