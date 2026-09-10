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
    "青色": Color.CYAN,
    "粉色": Color.PINK,
    "棕色": Color.BROWN,
}

_ALL_COLOR_NAMES = "|".join(_COLOR_BY_CHINESE_NAME)
_TRAY_COLOR_NAMES = "|".join(
    name for name, color in _COLOR_BY_CHINESE_NAME.items() if color.has_tray
)
_ASSEMBLY_PATTERN = re.compile(
    rf"(?P<block>{_ALL_COLOR_NAMES})\s*方块"
    rf"\s*(?:放到|放在|叠放到|叠放在)\s*"
    rf"(?:(?P<tray>{_TRAY_COLOR_NAMES})\s*托盘上|"
    rf"(?P<stack>{_ALL_COLOR_NAMES})\s*方块上)"
)


def parse_assembly_instruction(instruction_text: str) -> tuple[AssemblyStep, ...]:

    'Extract ordered color pairs from the task-card wording in the project book.'
    matches = tuple(_ASSEMBLY_PATTERN.finditer(instruction_text))
    if not matches:
        raise TaskPlanValidationError("没有从任务卡 2 中识别到装配指令。")

    steps = []
    for index, match in enumerate(matches, start=1):
        block_color = _COLOR_BY_CHINESE_NAME[match.group("block")]
        tray_name = match.group("tray")
        if tray_name is not None:
            steps.append(
                AssemblyStep.from_colors(
                    index, block_color, _COLOR_BY_CHINESE_NAME[tray_name]
                )
            )
        else:
            steps.append(
                AssemblyStep.from_stack(
                    index, block_color, _COLOR_BY_CHINESE_NAME[match.group("stack")]
                )
            )
    return tuple(steps)


def validate_task_plan(
    plan: TaskPlan,
    measurements: Mapping[tuple[Color, EntityKind], VisionMeasurement],
) -> TaskPlan:

    'Enforce all non-negotiable task-card and fixed-data invariants.'
    if not plan.task1_summary.strip():
        raise TaskPlanValidationError("任务卡 1 场景结论不能为空。")
    if not plan.task2_instruction.strip():
        raise TaskPlanValidationError("任务卡 2 装配指令不能为空。")
    _validate_legacy_plan(plan)
    expected_measurement_keys = {
        (step.block_color, EntityKind.BLOCK) for step in plan.steps
    } | {
        (step.destination_color, EntityKind.TRAY)
        for step in plan.steps
        if not step.is_stack
    }
    missing = expected_measurement_keys.difference(measurements)
    if missing:
        raise TaskPlanValidationError(f"缺少 {len(missing)} 组项目书规定的 VM 坐标数据。")
    return plan


def validate_offline_task_plan(plan: TaskPlan) -> TaskPlan:
    'Validate a randomized task-2 plan without depending on task-1 order.'
    if not plan.task2_instruction.strip():
        raise TaskPlanValidationError("任务卡 2 装配指令不能为空。")
    _validate_final_plan(plan)
    return plan


def _validate_legacy_plan(plan: TaskPlan) -> None:
    if len(plan.steps) != 6:
        raise TaskPlanValidationError("装配计划必须恰好包含 6 个方块到托盘步骤。")
    used_blocks: set[Color] = set()
    used_trays: set[Color] = set()
    for expected_index, step in enumerate(plan.steps, start=1):
        if step.index != expected_index or step.is_stack:
            raise TaskPlanValidationError("装配步骤必须从 1 到 6 连续且均为方块到托盘。")
        if step.block_trigger != step.block_color.block_trigger:
            raise TaskPlanValidationError(f"第 {step.index} 步方块触发码与颜色映射不一致。")
        if step.tray_trigger != step.destination_color.tray_trigger:
            raise TaskPlanValidationError(f"第 {step.index} 步托盘触发码与颜色映射不一致。")
        if step.block_color in used_blocks or step.destination_color in used_trays:
            raise TaskPlanValidationError("同一方块或托盘不能被重复使用。")
        used_blocks.add(step.block_color)
        used_trays.add(step.destination_color)


def _validate_final_plan(plan: TaskPlan) -> None:
    if len(plan.steps) != 7:
        raise TaskPlanValidationError("装配计划必须恰好包含 7 个步骤。")
    used_blocks: set[Color] = set()
    used_trays: set[Color] = set()
    for expected_index, step in enumerate(plan.steps, start=1):
        if step.index != expected_index:
            raise TaskPlanValidationError("装配步骤编号必须从 1 到 7 连续递增。")
        if step.block_trigger != step.block_color.block_trigger:
            raise TaskPlanValidationError(
                f"第 {step.index} 步方块触发码与颜色映射不一致。"
            )
        if step.block_color in used_blocks:
            raise TaskPlanValidationError("同一个方块不能被重复装配。")
        if expected_index <= 6:
            if step.is_stack:
                raise TaskPlanValidationError("前 6 步必须是方块装配到托盘。")
            if step.tray_trigger != step.destination_color.tray_trigger:
                raise TaskPlanValidationError(
                    f"第 {step.index} 步托盘触发码与颜色映射不一致。"
                )
            if step.destination_color in used_trays:
                raise TaskPlanValidationError("同一个托盘不能被重复使用。")
            used_trays.add(step.destination_color)
        else:
            if not step.is_stack:
                raise TaskPlanValidationError("第 7 步必须是方块叠放到方块。")
            if step.tray_trigger != step.destination_color.block_trigger:
                raise TaskPlanValidationError("第 7 步目标方块触发码与颜色映射不一致。")
            if step.destination_color not in used_blocks:
                raise TaskPlanValidationError("第 7 步只能叠放到前 6 步已经装配的方块上。")
        used_blocks.add(step.block_color)
