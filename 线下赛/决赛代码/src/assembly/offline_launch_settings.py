'Runtime-only connection values for the offline competition computer.'

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .settings import CompetitionConfig
from .visionmaster_tcp import VisionMasterCollectionConfig


@dataclass(frozen=True, slots=True)
class OfflineLaunchSettings:
    api_key: str
    visionmaster_host: str
    visionmaster_port: int
    arcs_host: str
    arcs_port: int
    task_card_image_directory: str = "C:/VisionMaster/task_cards"

    @property
    def robot_and_arcs_host(self) -> str:
        return self.arcs_host

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("请填写百炼 API 密钥。")
        if not self.visionmaster_host.strip():
            raise ValueError("请填写 VisionMaster IP。")
        if not self.arcs_host.strip():
            raise ValueError("请填写 ARCS 控制器 IP。")
        if not self.task_card_image_directory.strip():
            raise ValueError("请填写任务卡图片文件夹。")
        if not Path(self.task_card_image_directory).is_absolute():
            raise ValueError("任务卡图片文件夹必须填写绝对路径。")
        for label, value in (
            ("VisionMaster 端口", self.visionmaster_port),
            ("ARCS JSON-RPC 端口", self.arcs_port),
        ):
            if not 1 <= value <= 65535:
                raise ValueError(f"{label}必须在 1 到 65535 之间。")

    def apply_to(self, template: CompetitionConfig) -> CompetitionConfig:
        visionmaster = VisionMasterCollectionConfig(
            tcp=replace(
                template.visionmaster.tcp,
                host=self.visionmaster_host,
                port=self.visionmaster_port,
                task_card_image_directory=self.task_card_image_directory,
            ),
            retry_interval_s=template.visionmaster.retry_interval_s,
            collection_timeout_s=template.visionmaster.collection_timeout_s,
        )
        return replace(
            template,
            visionmaster=visionmaster,
            vmware=replace(template.vmware, arcs_guest_ip=self.arcs_host),
            aubo_arcs=replace(
                template.aubo_arcs,
                host=self.arcs_host,
                port=self.arcs_port,
            ),
        )

    @classmethod
    def from_form(
        cls,
        *,
        api_key: str,
        visionmaster_host: str,
        visionmaster_port: str,
        arcs_host: str,
        arcs_port: str,
        task_card_image_directory: str,
    ) -> "OfflineLaunchSettings":
        try:
            vm_port = int(visionmaster_port.strip())
            robot_port = int(arcs_port.strip())
        except ValueError as exc:
            raise ValueError("端口必须填写整数。") from exc
        return cls(
            api_key.strip(),
            visionmaster_host.strip(),
            vm_port,
            arcs_host.strip(),
            robot_port,
            task_card_image_directory.strip(),
        )
