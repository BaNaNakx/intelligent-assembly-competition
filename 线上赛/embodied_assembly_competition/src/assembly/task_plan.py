'Validation and deterministic parsing of competition assembly instructions.'

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import (
    AssemblyStep,
    Color,
    EntityKind,
    TaskPlan,
    VisionMeasurement,
)


class TaskPlanValidationError(ValueError):
    'Raised before any motion is planned when a task plan is unsafe or invalid.'
    pass


_COLOR_BY_CHINESE_NAME: Mapping[str, Color] = {
    "红色": Color.RED,
    "橙色": Color.ORANGE,
    "黄色": Color.YELLOW,
    "绿色": Color.GREEN,
    "蓝色": Color.BLUE,
    "紫色": Color.PURPLE,
}

_ASSEMBLY_PATTERN = re.compile(
    r"(?P<block>红色|橙色|黄色|绿色|蓝色|紫色)\s*方块"
    r"\s*放到\s*"
    r"(?P<tray>红色|橙色|黄色|绿色|蓝色|紫色)\s*托盘上"
)


def parse_assembly_instruction(instruction_text: str) -> tuple[AssemblyStep, ...]:

    'Extract ordered color pairs from the task-card wording in the project book.'
    matches = tuple(_ASSEMBLY_PATTERN.finditer(instruction_text))
    if not matches:
        raise TaskPlanValidationError("没有从任务卡 2 中识别到“方块放到托盘上”的指令。")

    return tuple(
        AssemblyStep.from_colors(
            index=index,
            block_color=_COLOR_BY_CHINESE_NAME[match.group("block")],
            tray_color=_COLOR_BY_CHINESE_NAME[match.group("tray")],
        )
        for index, match in enumerate(matches, start=1)
    )


def validate_task_plan(
    plan: TaskPlan,
    measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
) -> TaskPlan:

    'Enforce all non-negotiable task-card and fixed-data invariants.'
    if not plan.task1_summary.strip():
        raise TaskPlanValidationError("任务卡 1 场景结论不能为空。")
    if not plan.task2_instruction.strip():
        raise TaskPlanValidationError("任务卡 2 装配指令不能为空。")
    if len(plan.steps) != 6:
        raise TaskPlanValidationError("装配计划必须恰好包含 6 个步骤。")

    expected_measurement_keys = {
        (color, kind) for color in Color for kind in EntityKind
    }
    missing = expected_measurement_keys.difference(measurements)
    if missing:
        raise TaskPlanValidationError(f"缺少 {len(missing)} 组项目书规定的 VM 坐标数据。")

    used_blocks: set[Color] = set()
    used_trays: set[Color] = set()
    for expected_index, step in enumerate(plan.steps, start=1):
        if step.index != expected_index:
            raise TaskPlanValidationError("装配步骤编号必须从 1 到 6 连续递增。")
        if step.block_trigger != step.block_color.block_trigger:
            raise TaskPlanValidationError(
                f"第 {step.index} 步方块触发码与颜色映射不一致。"
            )
        if step.tray_trigger != step.tray_color.tray_trigger:
            raise TaskPlanValidationError(
                f"第 {step.index} 步托盘触发码与颜色映射不一致。"
            )
        if step.block_color in used_blocks:
            raise TaskPlanValidationError("同一个方块不能被重复装配。")
        if step.tray_color in used_trays:
            raise TaskPlanValidationError("同一个托盘不能被重复使用。")
        used_blocks.add(step.block_color)
        used_trays.add(step.tray_color)

    return plan
