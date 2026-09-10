'Ephemeral one-click competition settings entered through the GUI.'

from __future__ import annotations

from dataclasses import dataclass, replace

from .settings import CompetitionConfig
from .visionmaster_tcp import VisionMasterCollectionConfig


@dataclass(frozen=True, slots=True)
class CompetitionLaunchSettings:

    'Runtime-only secrets and endpoints; no field is written back to disk.'
    api_key: str
    visionmaster_host: str
    visionmaster_port: int
    arcs_host: str
    arcs_port: int

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("请填写百炼 API 密钥。")
        if not self.visionmaster_host.strip():
            raise ValueError("请填写 VisionMaster IP。")
        if not self.arcs_host.strip():
            raise ValueError("请填写 ARCS/VMware 虚拟机 IP。")
        for label, value in (
            ("VisionMaster 端口", self.visionmaster_port),
            ("ARCS JSON-RPC 端口", self.arcs_port),
        ):
            if not 1 <= value <= 65535:
                raise ValueError(f"{label}必须在 1 到 65535 之间。")

    @classmethod
    def from_form(
        cls,
        *,
        api_key: str,
        visionmaster_host: str,
        visionmaster_port: str,
        arcs_host: str,
        arcs_port: str,
    ) -> "CompetitionLaunchSettings":
        try:
            parsed_visionmaster_port = int(visionmaster_port.strip())
            parsed_arcs_port = int(arcs_port.strip())
        except ValueError as exc:
            raise ValueError("端口必须填写整数。") from exc
        return cls(
            api_key=api_key.strip(),
            visionmaster_host=visionmaster_host.strip(),
            visionmaster_port=parsed_visionmaster_port,
            arcs_host=arcs_host.strip(),
            arcs_port=parsed_arcs_port,
        )

    def apply_to(self, template: CompetitionConfig) -> CompetitionConfig:

        'Apply GUI values in memory and reuse the ARCS address as VMware IP.'
        visionmaster = VisionMasterCollectionConfig(
            tcp=replace(
                template.visionmaster.tcp,
                host=self.visionmaster_host,
                port=self.visionmaster_port,
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
