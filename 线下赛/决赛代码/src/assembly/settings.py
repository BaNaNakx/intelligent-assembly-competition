'Load deployment settings that must be prepared before the competition.'

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .visionmaster_tcp import VisionMasterCollectionConfig, VisionMasterTcpConfig


class ConfigurationError(ValueError):
    'Raised when the pre-competition configuration file is incomplete.'
    pass


@dataclass(frozen=True, slots=True)
class VmwareNetworkConfig:
    network_mode: str
    arcs_guest_ip: str

    def __post_init__(self) -> None:
        if self.network_mode not in {"bridged", "host_only", "nat"}:
            raise ValueError("VMware 网络模式只能是 bridged、host_only 或 nat。")
        if not self.arcs_guest_ip.strip():
            raise ValueError("ARCS 虚拟机 IP 不能为空。")


@dataclass(frozen=True, slots=True)
class AuboArcsConnectionConfig:
    host: str
    port: int
    robot_name: str
    request_timeout_s: float

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("ARCS 主机地址不能为空。")
        if not 1 <= self.port <= 65535:
            raise ValueError("ARCS 端口必须在 1 到 65535 之间。")
        if not self.robot_name.strip():
            raise ValueError("ARCS 机器人名称不能为空。")
        if self.request_timeout_s <= 0:
            raise ValueError("ARCS 请求超时必须大于 0。")


@dataclass(frozen=True, slots=True)
class QwenVisionConfig:

    'Connection settings for the pre-selected Qwen visual model.'
    base_url: str
    model: str
    request_timeout_s: float

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("https://", "http://")):
            raise ValueError("大模型 base_url 必须以 http:// 或 https:// 开头。")
        if not self.model.strip():
            raise ValueError("大模型 model 不能为空。")
        if self.request_timeout_s <= 0:
            raise ValueError("大模型请求超时必须大于 0。")


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    visionmaster: VisionMasterCollectionConfig
    vmware: VmwareNetworkConfig
    aubo_arcs: AuboArcsConnectionConfig
    llm: QwenVisionConfig


def load_visionmaster_collection_config(path: Path) -> VisionMasterCollectionConfig:

    'Load only the VisionMaster portion for the image collection entrypoint.'
    return load_competition_config(path).visionmaster


def load_competition_config(path: Path) -> CompetitionConfig:

    'Load all network addresses that must be prepared before a competition.'
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"找不到赛前配置文件：{path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"赛前配置文件不是有效 TOML：{path}") from exc

    visionmaster = document.get("visionmaster")
    vmware = document.get("vmware")
    aubo_arcs = document.get("aubo_arcs")
    llm = document.get("llm")
    if not isinstance(visionmaster, dict):
        raise ConfigurationError("配置文件缺少 [visionmaster] 区域。")
    if not isinstance(vmware, dict):
        raise ConfigurationError("配置文件缺少 [vmware] 区域。")
    if not isinstance(aubo_arcs, dict):
        raise ConfigurationError("配置文件缺少 [aubo_arcs] 区域。")
    if not isinstance(llm, dict):
        raise ConfigurationError("配置文件缺少 [llm] 区域。")

    try:
        tcp = VisionMasterTcpConfig(
            host=_required_str(visionmaster, "host"),
            port=_required_int(visionmaster, "port"),
            connect_timeout_s=_required_number(visionmaster, "connect_timeout_s"),
            idle_timeout_s=_required_number(visionmaster, "idle_timeout_s"),
            max_image_bytes=_required_int(visionmaster, "max_image_bytes"),
            chunk_size=_required_int(visionmaster, "chunk_size"),
            task_card_image_directory=_required_str(
                visionmaster, "task_card_image_directory"
            ),
            task_card_capture_timeout_s=float(visionmaster.get("task_card_capture_timeout_s", 15.0)),
        )
        visionmaster_config = VisionMasterCollectionConfig(
            tcp=tcp,
            retry_interval_s=_required_number(visionmaster, "retry_interval_s"),
            collection_timeout_s=_required_number(
                visionmaster, "collection_timeout_s"
            ),
        )
        vmware_config = VmwareNetworkConfig(
            network_mode=_required_str(vmware, "network_mode"),
            arcs_guest_ip=_required_str(vmware, "arcs_guest_ip"),
        )
        arcs_config = AuboArcsConnectionConfig(
            host=_required_str(aubo_arcs, "host"),
            port=_required_int(aubo_arcs, "port"),
            robot_name=_required_str(aubo_arcs, "robot_name"),
            request_timeout_s=_required_number(aubo_arcs, "request_timeout_s"),
        )
        llm_config = QwenVisionConfig(
            base_url=_required_str(llm, "base_url"),
            model=_required_str(llm, "model"),
            request_timeout_s=_required_number(llm, "request_timeout_s"),
        )
        return CompetitionConfig(
            visionmaster=visionmaster_config,
            vmware=vmware_config,
            aubo_arcs=arcs_config,
            llm=llm_config,
        )
    except ValueError as exc:
        raise ConfigurationError(f"赛前配置无效：{exc}") from exc


def _required_str(section: dict[str, Any], name: str) -> str:
    value = section.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"[visionmaster].{name} 必须是非空字符串。")
    return value


def _required_int(section: dict[str, Any], name: str) -> int:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"[visionmaster].{name} 必须是整数。")
    return value


def _required_number(section: dict[str, Any], name: str) -> float:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"[visionmaster].{name} 必须是数字。")
    return float(value)
