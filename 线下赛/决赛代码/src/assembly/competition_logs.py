'Competition evidence collection and three-file export.'

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

from .arcs_jsonrpc import ArcsTraceRecord
from .models import SceneResult, TaskPlan
from .qwen_vision import format_task1_final_output


_STEP_PREFIXES = ("先把", "再把", "接着把", "然后把", "再把", "再把", "最后把")
_CHINESE_NUMBERS = ("一", "二", "三", "四", "五", "六", "七")


@dataclass(frozen=True, slots=True)
class VmLogRecord:
    sequence: int
    requested_at: datetime
    received_at: datetime
    request_text: str
    response_text: str
    status: str


@dataclass(frozen=True, slots=True)
class StepExecutionResult:
    step_index: int
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CompetitionEvidenceSnapshot:
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    vm_records: tuple[VmLogRecord, ...]
    task1_result: SceneResult | None
    task2_plan: TaskPlan | None
    step_results: tuple[StepExecutionResult, ...]


class CompetitionEvidenceStore:
    def __init__(self, run_id: str, started_at: datetime) -> None:
        self._run_id = run_id
        self._started_at = started_at
        self._finished_at: datetime | None = None
        self._status = "运行中"
        self._vm_records: list[VmLogRecord] = []
        self._task1_result: SceneResult | None = None
        self._task2_plan: TaskPlan | None = None
        self._step_results: dict[int, StepExecutionResult] = {}
        self._lock = Lock()

    @property
    def run_id(self) -> str:
        return self._run_id

    def record_vm(
        self,
        *,
        requested_at: datetime,
        received_at: datetime,
        request_text: str,
        response_text: str,
        status: str,
    ) -> None:
        with self._lock:
            self._vm_records.append(
                VmLogRecord(
                    sequence=len(self._vm_records) + 1,
                    requested_at=requested_at,
                    received_at=received_at,
                    request_text=request_text,
                    response_text=response_text,
                    status=status,
                )
            )

    def record_task1(self, result: SceneResult) -> None:
        with self._lock:
            self._task1_result = result

    def record_task2(self, plan: TaskPlan) -> None:
        with self._lock:
            self._task2_plan = plan
            self._step_results = {
                step.index: StepExecutionResult(step.index, "待执行")
                for step in plan.steps
            }

    def mark_step_completed(self, step_index: int) -> None:
        with self._lock:
            self._step_results[step_index] = StepExecutionResult(step_index, "已完成")

    def mark_step_failed(self, step_index: int, detail: str) -> None:
        with self._lock:
            self._step_results[step_index] = StepExecutionResult(
                step_index, "执行失败", detail
            )

    def finish(self, status: str) -> bool:
        with self._lock:
            if self._finished_at is not None:
                return False
            self._status = status
            self._finished_at = datetime.now().astimezone()
            return True

    def snapshot(self) -> CompetitionEvidenceSnapshot:
        with self._lock:
            return CompetitionEvidenceSnapshot(
                run_id=self._run_id,
                started_at=self._started_at,
                finished_at=self._finished_at,
                status=self._status,
                vm_records=tuple(self._vm_records),
                task1_result=self._task1_result,
                task2_plan=self._task2_plan,
                step_results=tuple(
                    self._step_results[index] for index in sorted(self._step_results)
                ),
            )


@dataclass(frozen=True, slots=True)
class LogExportResult:
    vm_path: Path
    llm_path: Path
    arcs_path: Path


class CompetitionLogExporter:
    def export(
        self,
        output_directory: Path,
        snapshot: CompetitionEvidenceSnapshot,
        arcs_records: tuple[ArcsTraceRecord, ...],
    ) -> LogExportResult:
        output_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        vm_path = output_directory / f"VisionMaster日志_{stamp}.txt"
        llm_path = output_directory / f"LLM最终输出_{stamp}.txt"
        arcs_path = output_directory / f"ARCS原生日志_{stamp}.txt"
        arcs_content = render_arcs_log(snapshot, arcs_records)
        self._write(vm_path, render_vm_log(snapshot))
        self._write(llm_path, render_llm_log(snapshot))
        self._write(arcs_path, arcs_content)
        return LogExportResult(vm_path, llm_path, arcs_path)

    @staticmethod
    def _write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8-sig", newline="\n")
        temporary.replace(path)


def render_vm_log(snapshot: CompetitionEvidenceSnapshot) -> str:
    lines = _header("VisionMaster运行日志", snapshot)
    if not snapshot.vm_records:
        lines.append("当前尚无VisionMaster通信记录。")
    for record in snapshot.vm_records:
        lines.extend(
            (
                "",
                f"序号：{record.sequence}",
                f"请求时间：{_format_time(record.requested_at)}",
                f"接收时间：{_format_time(record.received_at)}",
                f"请求：{record.request_text}",
                f"状态：{record.status}",
                f"输出数据：{record.response_text}",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def render_llm_log(snapshot: CompetitionEvidenceSnapshot) -> str:
    lines = _header("大模型最终输出", snapshot)
    lines.append("")
    if snapshot.task1_result is None:
        lines.append("当前尚未生成任务一最终结果。")
    else:
        lines.append(format_task1_final_output(snapshot.task1_result))

    lines.extend(("", "【任务二最终结果】"))
    if snapshot.task2_plan is None:
        lines.append("当前尚未生成任务二最终结果。")
    else:
        lines.append(f"原始装配指令：{snapshot.task2_plan.task2_instruction}")
        lines.append("结构化执行步骤：")
        results = {result.step_index: result for result in snapshot.step_results}
        for step in snapshot.task2_plan.steps:
            result = results.get(step.index, StepExecutionResult(step.index, "待执行"))
            prefix = _STEP_PREFIXES[step.index - 1]
            number = _CHINESE_NUMBERS[step.index - 1]
            status = result.status
            if result.detail:
                status = f"{status}：{result.detail}"
            if step.is_stack:
                description = (
                    f"{prefix}{step.block_color.display_name}方块放在"
                    f"{step.destination_color.display_name}方块上，"
                    f"{step.block_color.display_name.removesuffix('色')}（{step.block_trigger}）→"
                    f"{step.destination_color.display_name}物块装配位姿2"
                )
            else:
                description = (
                    f"{prefix}{step.block_color.display_name}方块放到"
                    f"{step.destination_color.display_name}托盘上，"
                    f"{step.block_color.display_name.removesuffix('色')}（{step.block_trigger}）→"
                    f"{step.destination_color.display_name}（{step.tray_trigger}）"
                )
            lines.append(f"步骤{number}：{description}，{status}。")
    return "\n".join(lines).rstrip() + "\n"


def render_arcs_log(
    snapshot: CompetitionEvidenceSnapshot,
    records: tuple[ArcsTraceRecord, ...],
) -> str:
    start_marker = f"COMPETITION_START run_id={snapshot.run_id}"
    end_marker = f"COMPETITION_END run_id={snapshot.run_id}"
    start_index = next(
        (index for index, record in enumerate(records) if start_marker in record.text),
        None,
    )
    if start_index is None:
        raise ValueError(
            "ARCS Trace.peek未返回本次比赛开始标记；当前ARCS版本可能不提供普通textmsg读取。"
        )
    end_index = next(
        (
            index
            for index in range(start_index, len(records))
            if end_marker in records[index].text
        ),
        None,
    )
    if end_index is None:
        raise ValueError("ARCS Trace.peek未返回本次比赛结束标记，请在比赛流程结束后导出。")
    selected = records[start_index : end_index + 1]
    lines = _header("ARCS原生Trace日志", snapshot)
    lines.append("数据来源：ARCS JSON-RPC rob1.Trace.peek")
    for index, record in enumerate(selected, start=1):
        lines.extend(
            (
                "",
                f"序号：{index}",
                f"ARCS时间戳：{record.timestamp}",
                f"级别：{record.level}",
                f"来源：{record.source}",
                f"代码：{record.code}",
                f"内容：{record.text}",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _header(title: str, snapshot: CompetitionEvidenceSnapshot) -> list[str]:
    return [
        f"【{title}】",
        f"比赛运行标识：{snapshot.run_id}",
        f"开始时间：{_format_time(snapshot.started_at)}",
        f"结束时间：{_format_time(snapshot.finished_at) if snapshot.finished_at else '尚未结束'}",
        f"比赛状态：{snapshot.status}",
    ]


def _format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
