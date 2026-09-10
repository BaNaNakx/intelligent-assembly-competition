'Concrete automatic competition loop used by the one-click desktop entry.'

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from time import sleep
from typing import Callable, Protocol

from .arcs_jsonrpc import (
    ArcsMotionSafetyStatus,
    ArcsRobotSnapshot,
    ArcsTraceRecord,
    AuboArcsJsonRpcClient,
)
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
from .offline_io import ArcsToolSuctionIo
from .offline_launch_settings import OfflineLaunchSettings
from .offline_motion import (
    PhysicalAssemblyExecutor,
    PhysicalAssemblyGeometry,
    PhysicalAssemblyMotionPlanner,
    PhysicalMotionExecutionConfig,
)
from .offline_vision import JustInTimeVisionLocator
from .offline_workflow import CurrentTaskCardReader, OfflineRoundWorkflow
from .offline_state import OfflineTaskKind
from .robot_parameters import RobotParameterStore
from .robot_process import AssemblyStepStateMachine, parse_step_actions
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

    def collect_measurement(self, trigger: str) -> VisionMeasurement: ...


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
                request = f"start,{step.block_trigger}"
                self._report(f"向 VisionMaster 发送物块请求：{request}")
                requested_at = datetime.now().astimezone()
                try:
                    measurement = self._card_collector.collect_measurement(step.block_trigger)
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
                request = f"start,{step.tray_trigger}"
                self._report(f"向 VisionMaster 发送托盘请求：{request}")
                requested_at = datetime.now().astimezone()
                try:
                    measurement = self._card_collector.collect_measurement(step.tray_trigger)
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
            request_text = "start,89" if number == 1 else "start,99"
            self._report(f"正在向 VisionMaster 请求任务卡 {number}…")
            requested_at = datetime.now().astimezone()
            try:
                image = self._card_collector.collect_card(number)
            except Exception as exc:
                self._evidence.record_vm(
                    requested_at=requested_at,
                    received_at=datetime.now().astimezone(),
                    request_text=request_text,
                    response_text=str(exc),
                    status="失败",
                )
                raise
            self._cards[number] = image
            self._evidence.record_vm(
                requested_at=requested_at,
                received_at=image.received_at,
                request_text=request_text,
                response_text=f"任务卡{number}固定图片文件读取成功，共{len(image.data)}字节",
                status="成功",
            )
            if self._card_observer is not None:
                self._card_observer(number, image)
            self._report(f"VisionMaster 任务卡 {number} 已接收（固定图片文件读取）。")


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
    read_robot_snapshot: Callable[[], ArcsRobotSnapshot] | None = None
    read_motion_status: Callable[[], ArcsMotionSafetyStatus] | None = None
    robot_parameters: RobotParameterStore | None = None

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

    def reset(self) -> None:
        self.cancel_event.set()
        self.emergency_stop()
        self._finish("用户重置", "RESET")

    def export_logs(self, output_directory: Path) -> LogExportResult:
        snapshot = self.evidence.snapshot()
        arcs_records = self.read_trace()
        return self.log_exporter.export(output_directory, snapshot, arcs_records)

    def robot_telemetry(self) -> tuple[ArcsRobotSnapshot, ArcsMotionSafetyStatus]:
        if self.read_robot_snapshot is None or self.read_motion_status is None:
            raise RuntimeError("当前会话未配置实时机械臂状态读取。")
        return self.read_robot_snapshot(), self.read_motion_status()

    def update_robot_parameters(self, values: Mapping[str, str]):
        if self.robot_parameters is None:
            raise RuntimeError("当前会话未配置机械臂参数热更新。")
        return self.robot_parameters.update_from_form(values)

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
        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
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


class ReportingPhysicalRobot:
    def __init__(
        self,
        executor: PhysicalAssemblyExecutor,
        report: ReporterPort,
        trace_message: Callable[[str], int],
        evidence: CompetitionEvidenceStore,
        parameter_store: RobotParameterStore,
    ) -> None:
        self._executor = executor
        self._report = report
        self._trace_message = trace_message
        self._evidence = evidence
        self._parameter_store = parameter_store

    def prepare_round(self) -> None:
        self._report("机械臂已就绪，将前往共用任务卡拍照位。")
        self._executor.prepare_round()
        self._trace_message("OFFLINE_ROBOT_READY direct_task1_photo")

    def move_task_card_photo(self, slot_number: int) -> None:
        self._executor.move_task_card_photo(slot_number)
        sleep(self._parameter_store.snapshot().photo_settle_s)
        self._report("机械臂已到达共用任务卡拍照位。")

    def move_block_photo(self) -> None:
        self._executor.move_block_photo()
        sleep(self._parameter_store.snapshot().photo_settle_s)
        self._report("机械臂已到达物块拍照位。")

    def move_tray_photo(self) -> None:
        self._executor.move_tray_photo()
        sleep(self._parameter_store.snapshot().photo_settle_s)
        self._report("机械臂已到达托盘拍照位。")

    def pick(self, step: AssemblyStep, measurement: VisionMeasurement) -> None:
        self._trace_message(
            f"STEP {step.index} PICK BLOCK={measurement.raw_packet}"
        )
        try:
            self._executor.pick(step, measurement)
        except Exception as exc:
            self._evidence.mark_step_failed(step.index, str(exc))
            raise
        self._report(f"步骤 {step.index}：抓取完成。")

    def place(self, step: AssemblyStep, measurement: VisionMeasurement) -> None:
        self._trace_message(
            f"STEP {step.index} PLACE TRAY={measurement.raw_packet}"
        )
        try:
            self._executor.place(step, measurement)
        except Exception as exc:
            self._evidence.mark_step_failed(step.index, str(exc))
            raise
        self._report(f"步骤 {step.index}：放置完成。")

    def stack(self, step: AssemblyStep) -> None:
        self._trace_message(
            f"STEP {step.index} STACK TARGET_BLOCK={step.destination_color.display_name}"
        )
        try:
            self._executor.stack(step)
        except Exception as exc:
            self._evidence.mark_step_failed(step.index, str(exc))
            raise
        self._report(
            f"步骤 {step.index}：{step.block_color.display_name}方块已叠放到"
            f"{step.destination_color.display_name}方块上。"
        )

    def return_photo(self) -> None:
        self._executor.return_photo()
        self._report("机械臂已返回物块拍照位。")

    def finish_round(self) -> None:
        self._executor.finish_round()
        self._trace_message("OFFLINE_SUCTION_OFF")


@dataclass(slots=True)
class OfflineCompetitionRunner:
    settings: OfflineLaunchSettings
    config: CompetitionConfig
    runtime_root: Path
    report: ReporterPort
    robot_parameters: RobotParameterStore
    model_stream_report: Callable[[ModelStreamEvent], None] | None = None
    progress: Callable[[dict], None] = lambda _event: None

    def create_session(self) -> CompetitionSession:
        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_id = f"offline_{run_stamp}"
        run_dir = self.runtime_root / run_stamp
        logger = AuditLogger(run_dir, "offline_competition")
        self.report(f"审计日志：{logger.text_path}")
        evidence = CompetitionEvidenceStore(run_id, datetime.now().astimezone())
        cancel_event = Event()

        def check_cancelled() -> None:
            if cancel_event.is_set():
                raise RuntimeError("流程已重置，已停止后续指令。")

        command_lock = Lock()

        def submit(action):
            with command_lock:
                check_cancelled()
                return action()

        collector = VisionMasterTaskCardCollector(self.config.visionmaster, cancel_event)

        def observe_model(event: ModelStreamEvent) -> None:
            logger.write(
                AuditEvent(
                    occurred_at=datetime.now().astimezone(),
                    event_type="model_stream",
                    state=TaskState.ASSEMBLING,
                    message=f"{event.stage}{event.kind}",
                    data={"text": event.text},
                )
            )

        model = QwenVisionClient(
            self.config.llm,
            self.settings.api_key,
            stream_observer=observe_model,
        )
        arcs_client = AuboArcsJsonRpcClient(self.config.aubo_arcs)
        monitor_client = AuboArcsJsonRpcClient(self.config.aubo_arcs)

        def stop_robot():
            with command_lock:
                result = arcs_client.emergency_stop()
                arcs_client.wait_until_steady(timeout_s=10.0, poll_interval_s=0.1)
                return result

        def observe_placement_pose(record):
            def values(pose):
                return (
                    pose.x_m * 1000.0,
                    pose.y_m * 1000.0,
                    pose.z_m * 1000.0,
                    pose.rx_rad,
                    pose.ry_rad,
                    pose.rz_rad,
                )
            self.progress({
                "phase": "placement_pose",
                "color": record.color.value,
                "display_color": record.color.display_name,
                "pose1": values(record.pose1),
                "pose2": values(record.pose2),
            })

        parameters = self.robot_parameters.snapshot()
        physical_executor = PhysicalAssemblyExecutor(
            arcs_client,
            ArcsToolSuctionIo(
                arcs_client,
                parameter_store=self.robot_parameters,
            ),
            PhysicalAssemblyMotionPlanner(
                PhysicalAssemblyGeometry.from_parameters(parameters)
            ),
            PhysicalMotionExecutionConfig.from_parameters(parameters),
            self.robot_parameters,
            check_cancelled=check_cancelled,
            submit=submit,
            placement_pose_observer=observe_placement_pose,
        )
        robot = ReportingPhysicalRobot(
            physical_executor,
            self.report,
            arcs_client.trace_textmsg,
            evidence,
            self.robot_parameters,
        )
        locator = JustInTimeVisionLocator(collector, self.report, evidence)
        step_process = AssemblyStepStateMachine(
            locator,
            robot,
            self.report,
            lambda: parse_step_actions(
                self.robot_parameters.snapshot().step_action_sequence
            ),
            check_cancelled=check_cancelled,
            progress=self.progress,
        )

        workflow = OfflineRoundWorkflow(
            cards=CurrentTaskCardReader(collector, evidence),
            model=model,
            step_process=step_process,
            robot=robot,
            evidence=evidence,
            logger=logger,
            task_sequence=lambda: tuple(
                OfflineTaskKind(value)
                for value in self.robot_parameters.snapshot().task_sequence
            ),
            result_report=self.report,
            after_model_result=lambda: sleep(
                self.robot_parameters.snapshot().model_result_settle_s
            ),
            check_cancelled=check_cancelled,
            progress=self.progress,
        )
        return CompetitionSession(
            workflow,
            cancel_event,
            stop_robot,
            arcs_client.trace_textmsg,
            lambda: arcs_client.peek_trace(max_records=10000, last_time=0),
            evidence,
            CompetitionLogExporter(),
            monitor_client.get_snapshot,
            monitor_client.get_motion_safety_status,
            self.robot_parameters,
        )
