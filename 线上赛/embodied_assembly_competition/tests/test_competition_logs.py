from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from assembly.competition_logs import (
    CompetitionEvidenceStore,
    CompetitionLogExporter,
    render_arcs_log,
    render_llm_log,
)
from assembly.arcs_jsonrpc import ArcsTraceRecord
from assembly.models import SceneResult, TaskPlan
from assembly.task_plan import parse_assembly_instruction


INSTRUCTION = (
    "先把红色方块放到蓝色托盘上，再把绿色方块放到红色托盘上，"
    "接着把橙色方块放到黄色托盘上，然后把蓝色方块放到紫色托盘上，"
    "再把黄色方块放到橙色托盘上，最后把紫色方块放到绿色托盘上。"
)


def make_store() -> CompetitionEvidenceStore:
    store = CompetitionEvidenceStore(
        "competition_20260812_120000", datetime.now().astimezone()
    )
    store.record_task1(
        SceneResult(
            "识别到六个物体。",
            "禁止导出的推理过程",
            ("电脑", "齿轮", "雨伞", "螺丝刀", "螺母", "水杯"),
            "1. 任务类型判断\n图片为任务卡1。\n2. 画面区域扫描\n识别六个区域。",
            "生活用品",
        )
    )
    store.record_task2(
        TaskPlan(
            "识别到六个物体。",
            INSTRUCTION,
            parse_assembly_instruction(INSTRUCTION),
            "禁止导出的任务二推理过程",
        )
    )
    store.mark_step_completed(1)
    return store


class CompetitionLogTests(unittest.TestCase):
    def test_llm_log_keeps_only_task1_final_output(self) -> None:
        text = render_llm_log(make_store().snapshot())

        self.assertIn("【任务一｜最终输出】", text)
        self.assertIn("场景类别：生活用品", text)
        self.assertIn("1. 电脑", text)
        self.assertIn("结果校验：数量为6，名称无重复，识别有效。", text)
        self.assertIn(
            "步骤一：先把红色方块放到蓝色托盘上，红（11）→蓝色（25），已完成。",
            text,
        )
        self.assertIn("步骤二：再把绿色方块放到红色托盘上", text)
        self.assertNotIn("【任务一｜千问视觉推理】", text)
        self.assertNotIn("1. 任务类型判断", text)
        self.assertNotIn("禁止导出的推理过程", text)
        self.assertNotIn("禁止导出的任务二推理过程", text)

    def test_export_creates_two_independent_text_files(self) -> None:
        store = make_store()
        now = datetime.now().astimezone()
        store.record_vm(
            requested_at=now,
            received_at=now,
            request_text="wukuai,11",
            response_text="#0;40;-40;40;",
            status="成功",
        )
        exporter = CompetitionLogExporter()
        arcs_records = (
            ArcsTraceRecord(
                100, "INFO", "rob1", 0,
                (f"COMPETITION_START run_id={store.run_id}",),
            ),
            ArcsTraceRecord(200, "INFO", "rob1", 0, ("STEP 1 VM_RX",)),
            ArcsTraceRecord(
                300, "INFO", "rob1", 0,
                (f"COMPETITION_END run_id={store.run_id} status=COMPLETED",),
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = exporter.export(Path(temp_dir), store.snapshot(), arcs_records)

            self.assertEqual(len(list(Path(temp_dir).glob("*.txt"))), 3)
            self.assertIn("#0;40;-40;40;", result.vm_path.read_text(encoding="utf-8-sig"))
            self.assertIn("步骤一", result.llm_path.read_text(encoding="utf-8-sig"))
            self.assertIn("STEP 1 VM_RX", result.arcs_path.read_text(encoding="utf-8-sig"))

    def test_arcs_log_contains_only_current_competition_interval(self) -> None:
        store = make_store()
        records = (
            ArcsTraceRecord(1, "INFO", "rob1", 0, ("OLD LOG",)),
            ArcsTraceRecord(
                2, "INFO", "rob1", 0,
                (f"COMPETITION_START run_id={store.run_id}",),
            ),
            ArcsTraceRecord(3, "INFO", "rob1", 0, ("STEP 1",)),
            ArcsTraceRecord(
                4, "INFO", "rob1", 0,
                (f"COMPETITION_END run_id={store.run_id} status=COMPLETED",),
            ),
            ArcsTraceRecord(5, "INFO", "rob1", 0, ("NEWER LOG",)),
        )

        text = render_arcs_log(store.snapshot(), records)

        self.assertIn("COMPETITION_START", text)
        self.assertIn("STEP 1", text)
        self.assertIn("COMPETITION_END", text)
        self.assertNotIn("OLD LOG", text)
        self.assertNotIn("NEWER LOG", text)

    def test_arcs_log_refuses_export_without_native_markers(self) -> None:
        store = make_store()
        records = (ArcsTraceRecord(1, "INFO", "rob1", 0, ("unrelated",)),)

        with self.assertRaisesRegex(ValueError, "开始标记"):
            render_arcs_log(store.snapshot(), records)
