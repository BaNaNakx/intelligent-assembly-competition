'Concrete automatic competition loop used by the one-click desktop entry.'

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable, Protocol

from .arcs_jsonrpc import ArcsTraceRecord, AuboArcsJsonRpcClient
from .arcs_motion import ArcsAssemblyExecutor
from .audit import AuditLogger
from .competition_logs import (
    CompetitionEvidenceStore,
    CompetitionLogExporter,
    LogExportResult,
)
from .launch_settings import CompetitionLaunchSettings
from .models import (
    AssemblyStep,
    AuditEvent,
    Color,
    EntityKind,
    SceneResult,
    TaskCard,
    TaskPlan,
    TaskState,
    VisionMeasurement,
)
from .qwen_vision import ModelStreamEvent, QwenVisionClient
from .settings import CompetitionConfig
from .visionmaster_tcp import (
    ReceivedTaskImage,
    VisionMasterTaskCardCollector,
)
from .vm_protocol import VisionMeasurementStore
from .workflow import CompetitionWorkflow


class ReporterPort(Protocol):
    def __call__(self, text: str) -> None: ...


class TaskCardCollectorPort(Protocol):
    def collect_card(self, card_number: int) -> ReceivedTaskImage: ...

    def collect_measurement(self, prefix: str, trigger: str) -> VisionMeasurement: ...


class VisionModelPort(Protocol):
    def analyze_task1(self, image) -> SceneResult: ...

    def analyze_task2(self, image, *, task1_summary: str) -> TaskPlan: ...


CardObserver = Callable[[int, ReceivedTaskImage], None]


class RuntimeTaskInput:

    'Request each task card only when the competition state reaches that task.'
    def __init__(
        self,
        card_collector: TaskCardCollectorPort,
        report: ReporterPort,
        evidence: CompetitionEvidenceStore,
        card_observer: CardObserver | None = None,
    ) -> None:
        self._card_collector = card_collector
        self._report = report
        self._evidence = evidence
        self._card_observer = card_observer
        self._cards: dict[int, ReceivedTaskImage] = {}
        self._measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement] | None = None

    def get_card(self, number: int) -> TaskCard:
        self._ensure_card(number)
        if number == 1:
            return TaskCard(number=1, scene_objects=("等待视觉大模型解析",))
        if number == 2:
            return TaskCard(number=2, instruction_text="等待视觉大模型解析")
        raise ValueError("任务卡编号只能是 1 或 2。")

    def get_image(self, number: int):
        self._ensure_card(number)
        return self._cards[number]

    def get_measurements(
        self, steps: Sequence[AssemblyStep]
    ) -> Mapping[tuple[Color, EntityKind], VisionMeasurement]:

        'Request one VM result per task-card trigger in agent order.'
        if self._measurements is None:
            blocks: list[VisionMeasurement] = []
            trays: list[VisionMeasurement] = []
            for step in steps:
                request = f"wukuai,{step.block_trigger}"
                self._report(f"向 VisionMaster 发送物块请求：{request}")
                requested_at = datetime.now().astimezone()
                try:
                    measurement = self._card_collector.collect_measurement(
                        "wukuai", step.block_trigger
                    )
                except Exception as exc:
                    self._evidence.record_vm(
                        requested_at=requested_at,
                        received_at=datetime.now().astimezone(),
                        request_text=request,
                        response_text=str(exc),
                        status="失败",
                    )
                    raise
                blocks.append(measurement)
                self._evidence.record_vm(
                    requested_at=requested_at,
                    received_at=measurement.received_at,
                    request_text=request,
                    response_text=measurement.raw_packet,
                    status="成功",
                )
            for step in steps:
                request = f"tuopan,{step.tray_trigger}"
                self._report(f"向 VisionMaster 发送托盘请求：{request}")
                requested_at = datetime.now().astimezone()
                try:
                    measurement = self._card_collector.collect_measurement(
                        "tuopan", step.tray_trigger
                    )
                except Exception as exc:
                    self._evidence.record_vm(
                        requested_at=requested_at,
                        received_at=datetime.now().astimezone(),
                        request_text=request,
                        response_text=str(exc),
                        status="失败",
                    )
                    raise
                trays.append(measurement)
                self._evidence.record_vm(
                    requested_at=requested_at,
                    received_at=measurement.received_at,
                    request_text=request,
                    response_text=measurement.raw_packet,
                    status="成功",
                )
            store = VisionMeasurementStore()
            for measurement in (*blocks, *trays):
                store.ingest(
                    measurement.raw_packet,
                    received_at=measurement.received_at,
                )
            self._measurements = store.snapshot()
        return self._measurements

    def _ensure_card(self, number: int) -> None:
        if number not in (1, 2):
            raise ValueError("任务卡编号只能是 1 或 2。")
        if number not in self._cards:
            self._report(f"正在向 VisionMaster 请求任务卡 {number}…")
            requested_at = datetime.now().astimezone()
            try:
                image = self._card_collector.collect_card(number)
            except Exception as exc:
                self._evidence.record_vm(
                    requested_at=requested_at,
                    received_at=datetime.now().astimezone(),
                    request_text="duqu",
                    response_text=str(exc),
                    status="失败",
                )
                raise
            self._cards[number] = image
            self._evidence.record_vm(
                requested_at=requested_at,
                received_at=image.received_at,
                request_text="duqu",
                response_text=f"任务卡{number}图片接收成功，共{len(image.data)}字节",
                status="成功",
            )
            if self._card_observer is not None:
                self._card_observer(number, image)
            self._report(f"VisionMaster 任务卡 {number} 已接收。")


class RuntimeVisionAgent:

    'Connect the two cached VisionMaster images to the Qwen visual model.'
    def __init__(
        self,
        task_input: RuntimeTaskInput,
        model: VisionModelPort,
        evidence: CompetitionEvidenceStore,
    ) -> None:
        self._task_input = task_input
        self._model = model
        self._evidence = evidence
        self._task1_summary: str | None = None

    def summarize_scene(self, card: TaskCard) -> SceneResult:
        if card.number != 1:
            raise ValueError("场景识别只能使用任务卡 1。")
        scene = self._model.analyze_task1(self._task_input.get_image(1))
        self._task1_summary = scene.summary
        self._evidence.record_task1(scene)
        return scene

    def build_assembly_plan(self, card: TaskCard) -> TaskPlan:
        if card.number != 2:
            raise ValueError("装配指令识别只能使用任务卡 2。")
        if self._task1_summary is None:
            raise ValueError("任务卡 2 解析前必须先完成任务卡 1 场景识别。")
        plan = self._model.analyze_task2(
            self._task_input.get_image(2),
            task1_summary=self._task1_summary,
        )
        self._evidence.record_task2(plan)
        return plan


class TextSpeaker:

    'Displays task conclusions in the competition window.'
    def __init__(self, report: ReporterPort) -> None:
        self._report = report

    def speak(self, text: str) -> None:
        self._report(f"文本输出：{text}")


class ReportingAssemblyExecutor:

    'Make every required intelligent-agent trigger visible before robot motion.'
    def __init__(
        self,
        executor: ArcsAssemblyExecutor,
        report: ReporterPort,
        cancel_event: Event,
        evidence: CompetitionEvidenceStore,
    ) -> None:
        self._executor = executor
        self._report = report
        self._cancel_event = cancel_event
        self._evidence = evidence

    def execute_step(
        self,
        step: AssemblyStep,
        measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
    ) -> None:
        if self._cancel_event.is_set():
            raise RuntimeError("五分钟演示计时已结束，拒绝启动新的机器人动作。")
        self._report(
            f"步骤 {step.index}：{step.block_color.display_name}方块({step.block_trigger}) "
            f"→ {step.tray_color.display_name}托盘({step.tray_trigger})，执行中。"
        )
        try:
            self._executor.execute_step(step, measurements)
        except Exception as exc:
            self._evidence.mark_step_failed(step.index, str(exc))
            raise
        self._evidence.mark_step_completed(step.index)
        self._report(
            f"步骤 {step.index}：{step.block_color.display_name}方块({step.block_trigger}) "
            f"→ {step.tray_color.display_name}托盘({step.tray_trigger})，已完成，已返回初始位姿。"
        )


@dataclass(slots=True)
class CompetitionSession:
    'Starts the complete competition workflow without text interaction.'

    workflow: CompetitionWorkflow
    cancel_event: Event
    emergency_stop: Callable[[], int]
    trace_message: Callable[[str], int]
    read_trace: Callable[[], tuple[ArcsTraceRecord, ...]]
    evidence: CompetitionEvidenceStore
    log_exporter: CompetitionLogExporter

    def start(self) -> None:
        if self.cancel_event.is_set():
            raise RuntimeError("五分钟演示计时已结束，不能启动比赛流程。")
        self.trace_message(f"COMPETITION_START run_id={self.evidence.run_id}")
        try:
            self.workflow.run()
        except Exception:
            self._finish("运行失败", "FAILED")
            raise
        self._finish("已完成", "COMPLETED")

    def stop_for_timeout(self) -> None:
        self.cancel_event.set()
        self._finish("计时结束", "TIMEOUT")
        self.emergency_stop()

    def export_logs(self, output_directory: Path) -> LogExportResult:
        snapshot = self.evidence.snapshot()
        arcs_records = self.read_trace()
        return self.log_exporter.export(output_directory, snapshot, arcs_records)

    def _finish(self, status: str, arcs_status: str) -> None:
        if self.evidence.finish(status):
            try:
                self.trace_message(
                    f"COMPETITION_END run_id={self.evidence.run_id} status={arcs_status}"
                )
            except Exception:
                pass


@dataclass(slots=True)
class CompetitionRunner:

    'Build all real adapters only after the user clicks the GUI start button.'
    settings: CompetitionLaunchSettings
    config: CompetitionConfig
    runtime_root: Path
    report: ReporterPort
    card_observer: CardObserver | None = None
    model_stream_report: Callable[[ModelStreamEvent], None] | None = None

    def create_session(self) -> CompetitionSession:
        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"competition_{run_stamp}"
        run_dir = self.runtime_root / run_stamp
        logger = AuditLogger(run_dir, "competition")
        self.report(f"审计日志：{logger.text_path}")

        cancel_event = Event()
        evidence = CompetitionEvidenceStore(run_id, datetime.now().astimezone())
        task_input = RuntimeTaskInput(
            VisionMasterTaskCardCollector(self.config.visionmaster),
            self.report,
            evidence,
            self.card_observer,
        )
        def observe_model(event: ModelStreamEvent) -> None:
            if self.model_stream_report is not None:
                self.model_stream_report(event)
            elif event.stage == "任务一" and event.kind == "最终输出":
                self.report(event.text)
            else:
                self.report(f"[{event.stage}][{event.kind}] {event.text}")
            logger.write(
                AuditEvent(
                    occurred_at=datetime.now().astimezone(),
                    event_type="model_stream",
                    state=(
                        TaskState.TASK1_RECOGNIZING
                        if event.stage == "任务一"
                        else TaskState.TASK2_RECOGNIZING
                    ),
                    message=f"{event.stage}{event.kind}",
                    data={"text": event.text},
                )
            )
        agent = RuntimeVisionAgent(
            task_input,
            QwenVisionClient(
                self.config.llm,
                self.settings.api_key,
                stream_observer=observe_model,
            ),
            evidence,
        )
        speaker = TextSpeaker(self.report)
        arcs_client = AuboArcsJsonRpcClient(self.config.aubo_arcs)
        executor = ReportingAssemblyExecutor(
            ArcsAssemblyExecutor(arcs_client),
            self.report,
            cancel_event,
            evidence,
        )
        workflow = CompetitionWorkflow(
            task_input=task_input,
            agent=agent,
            speaker=speaker,
            executor=executor,
            logger=logger,
        )
        log_exporter = CompetitionLogExporter()
        return CompetitionSession(
            workflow,
            cancel_event,
            arcs_client.emergency_stop,
            arcs_client.trace_textmsg,
            lambda: arcs_client.peek_trace(max_records=10000, last_time=0),
            evidence,
            log_exporter,
        )
