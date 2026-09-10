from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from assembly.audit import AuditLogger
from assembly.competition_logs import CompetitionEvidenceStore
from assembly.models import Color, EntityKind, SceneResult, TaskPlan, VisionMeasurement
from assembly.offline_state import OfflineRoundPhase, OfflineTaskKind
from assembly.offline_vision import JustInTimeVisionLocator
from assembly.offline_workflow import CurrentTaskCardReader, OfflineRoundWorkflow
from assembly.robot_parameters import DEFAULT_STEP_ACTIONS
from assembly.robot_process import AssemblyStepStateMachine, parse_step_actions
from assembly.task_plan import parse_assembly_instruction
from assembly.visionmaster_tcp import ReceivedTaskImage


INSTRUCTION = (
    "红色方块放到黄色托盘上，橙色方块放到绿色托盘上，"
    "黄色方块放到蓝色托盘上，绿色方块放到紫色托盘上，"
    "蓝色方块放到红色托盘上，紫色方块放到橙色托盘上"
)
INSTRUCTION += "，最后把粉色方块叠放在红色方块上。"


class FakeCollector:
    def __init__(self) -> None:
        self.card_count = 0
        self.card_numbers = []
        self.vm_requests = []

    def collect_card(self, card_number):
        self.card_count += 1
        self.card_numbers.append(card_number)
        return ReceivedTaskImage(
            f"BM-card-{self.card_count}".encode(),
            datetime.now().astimezone(),
            "vm",
            7930,
        )

    def collect_measurement(self, trigger):
        self.vm_requests.append(trigger)
        kind = EntityKind.BLOCK if trigger.startswith("1") else EntityKind.TRAY
        color = next(
            color
            for color in Color
            if (
                trigger == color.block_trigger
                if kind is EntityKind.BLOCK
                else color.has_tray and trigger == color.tray_trigger
            )
        )
        return VisionMeasurement(
            color,
            kind,
            float(trigger),
            10.0,
            0.0,
            f"#{trigger};10;0",
            datetime.now().astimezone(),
        )


class FakeModel:
    def __init__(self) -> None:
        self.task1_images = []
        self.task2_images = []
        self.classification_calls = 0

    def identify_task_card(self, image):
        self.classification_calls += 1
        return next(self.classifications)

    def analyze_task1(self, image):
        self.task1_images.append(image.data)
        return SceneResult(
            "识别到六个生活物品",
            "trace1",
            tuple(f"物体{i}" for i in range(6)),
            "完整推理",
        )

    def analyze_task2(self, image, *, task1_summary=""):
        self.task2_images.append(image.data)
        return TaskPlan(
            "",
            INSTRUCTION,
            parse_assembly_instruction(INSTRUCTION),
            "trace2",
        )


class FakeRobot:
    def __init__(self) -> None:
        self.prepared = 0
        self.finished = 0
        self.actions = []
        self.card_photo_slots = []

    def prepare_round(self):
        self.prepared += 1

    def move_task_card_photo(self, slot_number):
        self.card_photo_slots.append(slot_number)

    def move_block_photo(self):
        self.actions.append((0, "move_block_photo", None))

    def move_tray_photo(self):
        self.actions.append((0, "move_tray_photo", None))

    def pick(self, step, measurement):
        self.actions.append((step.index, "pick", measurement.key))

    def place(self, step, measurement):
        self.actions.append((step.index, "place", measurement.key))

    def stack(self, step):
        self.actions.append((step.index, "stack", step.destination_color))

    def return_photo(self):
        self.actions.append((0, "return_photo", None))

    def finish_round(self):
        self.finished += 1


def build_workflow(directory, task_sequence, *, preflight=None, reports=None, waits=None):
    now = datetime.now().astimezone()
    evidence = CompetitionEvidenceStore("run", now)
    collector = FakeCollector()
    robot = FakeRobot()
    locator = JustInTimeVisionLocator(collector, lambda text: None, evidence)
    process = AssemblyStepStateMachine(
        locator,
        robot,
        lambda text: None,
        parse_step_actions(DEFAULT_STEP_ACTIONS),
    )
    model = FakeModel()
    model.classifications = iter(task_sequence)
    workflow = OfflineRoundWorkflow(
        cards=CurrentTaskCardReader(collector, evidence),
        model=model,
        step_process=process,
        robot=robot,
        evidence=evidence,
        logger=AuditLogger(Path(directory), "offline"),
        task_sequence=task_sequence,
        preflight=preflight,
        result_report=None if reports is None else reports.append,
        after_model_result=None if waits is None else lambda: waits.append(True),
    )
    return workflow, collector, robot, model


class OfflineWorkflowTests(unittest.TestCase):
    def test_failed_preflight_does_not_touch_robot(self) -> None:
        with TemporaryDirectory() as directory:
            workflow, _, robot, _ = build_workflow(
                directory,
                (OfflineTaskKind.TASK2,),
                preflight=lambda: (_ for _ in ()).throw(RuntimeError("not ready")),
            )
            with self.assertRaisesRegex(RuntimeError, "not ready"):
                workflow.run()
        self.assertEqual(robot.prepared, 0)
        self.assertEqual(robot.finished, 0)

    def test_entry_directly_runs_task2(self) -> None:
        with TemporaryDirectory() as directory:
            workflow, collector, robot, _ = build_workflow(
                directory,
                (OfflineTaskKind.TASK2,),
            )
            workflow.run()

        self.assertEqual(workflow.state.snapshot().phase, OfflineRoundPhase.COMPLETED)
        self.assertEqual(collector.card_count, 1)
        self.assertEqual(collector.card_numbers, [2])
        self.assertEqual(robot.card_photo_slots, [1])
        self.assertEqual(len(collector.vm_requests), 13)
        self.assertEqual(collector.vm_requests[:2], ["11", "23"])
        self.assertEqual(
            [action[1] for action in robot.actions[:4]],
            ["move_block_photo", "pick", "move_tray_photo", "place"],
        )
        self.assertEqual(len([action for action in robot.actions if action[1] == "pick"]), 7)
        self.assertEqual(len([action for action in robot.actions if action[1] == "place"]), 6)
        self.assertEqual(len([action for action in robot.actions if action[1] == "stack"]), 1)
        self.assertEqual(robot.finished, 1)

    def test_task_order_is_configurable(self) -> None:
        with TemporaryDirectory() as directory:
            workflow, collector, robot, model = build_workflow(
                directory,
                (OfflineTaskKind.TASK2, OfflineTaskKind.TASK1),
            )
            workflow.run()
        self.assertEqual(collector.card_count, 2)
        self.assertEqual(collector.card_numbers, [2, 1])
        self.assertEqual(robot.card_photo_slots, [1, 1])
        self.assertEqual(model.classification_calls, 0)
        self.assertEqual(workflow.state.snapshot().phase, OfflineRoundPhase.COMPLETED)

    def test_both_cards_use_common_photo_position(self) -> None:
        reports = []
        waits = []
        with TemporaryDirectory() as directory:
            workflow, collector, robot, model = build_workflow(
                directory,
                (OfflineTaskKind.TASK1, OfflineTaskKind.TASK2),
                reports=reports,
                waits=waits,
            )
            workflow.run()

        self.assertEqual(collector.card_count, 2)
        self.assertEqual(robot.card_photo_slots, [1, 1])
        self.assertEqual(model.task1_images, [b"BM-card-1"])
        self.assertEqual(model.task2_images, [b"BM-card-2"])
        self.assertEqual(model.classification_calls, 0)
        self.assertEqual(waits, [True, True])
        self.assertEqual(reports[0], "任务卡1识别结果：物体0、物体1、物体2、物体3、物体4、物体5")
        self.assertTrue(reports[1].startswith("任务卡2装配内容：红色方块"))
        self.assertEqual(workflow.state.snapshot().phase, OfflineRoundPhase.COMPLETED)


if __name__ == "__main__":
    unittest.main()
