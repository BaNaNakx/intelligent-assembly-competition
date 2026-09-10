'Just-in-time VisionMaster positioning for the randomized offline workspace.'

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from .competition_logs import CompetitionEvidenceStore
from .models import AssemblyStep, Color, EntityKind, VisionMeasurement


class OfflineVisionError(ValueError):
    pass


class MeasurementCollectorPort(Protocol):
    def collect_measurement(self, trigger: str) -> VisionMeasurement: ...


class ReporterPort(Protocol):
    def __call__(self, text: str) -> None: ...


class JustInTimeVisionLocator:
    def __init__(
        self,
        collector: MeasurementCollectorPort,
        report: ReporterPort,
        evidence: CompetitionEvidenceStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._collector = collector
        self._report = report
        self._evidence = evidence
        self._clock = clock or (lambda: datetime.now().astimezone())

    def locate_step(
        self, step: AssemblyStep
    ) -> Mapping[tuple[Color, EntityKind], VisionMeasurement]:
        block = self._request(
            step.block_trigger,
            (step.block_color, EntityKind.BLOCK),
        )
        tray = self._request(
            step.tray_trigger,
            (step.tray_color, EntityKind.TRAY),
        )
        return {block.key: block, tray.key: tray}

    def locate_block(self, step: AssemblyStep) -> VisionMeasurement:
        return self._request(
            step.block_trigger,
            (step.block_color, EntityKind.BLOCK),
        )

    def locate_tray(self, step: AssemblyStep) -> VisionMeasurement:
        return self._request(
            step.tray_trigger,
            (step.tray_color, EntityKind.TRAY),
        )

    def _request(
        self,
        trigger: str,
        expected_key: tuple[Color, EntityKind],
    ) -> VisionMeasurement:
        from .visionmaster_tcp import measurement_request
        request_text = measurement_request(trigger)
        requested_at = self._clock()
        self._report(f"向 VisionMaster 发送实时定位请求：{request_text}")
        try:
            measurement = self._collector.collect_measurement(trigger)
            if measurement.key != expected_key:
                raise OfflineVisionError(
                    f"VisionMaster 请求 {request_text} 返回了错误的颜色或目标类别。"
                )
            if _local_time(measurement.received_at) < _local_time(requested_at):
                raise OfflineVisionError(
                    f"VisionMaster 请求 {request_text} 返回了请求前产生的旧坐标。"
                )
        except Exception as exc:
            self._evidence.record_vm(
                requested_at=requested_at,
                received_at=self._clock(),
                request_text=request_text,
                response_text=str(exc),
                status="失败",
            )
            raise
        self._evidence.record_vm(
            requested_at=requested_at,
            received_at=measurement.received_at,
            request_text=request_text,
            response_text=measurement.raw_packet,
            status="成功",
        )
        return measurement


def _local_time(value: datetime) -> datetime:
    return value.astimezone() if value.tzinfo is not None else value.astimezone()
