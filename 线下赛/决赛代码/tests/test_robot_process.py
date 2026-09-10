from __future__ import annotations

import unittest
from datetime import datetime

from assembly.models import AssemblyStep, Color, EntityKind, VisionMeasurement
from assembly.robot_process import (
    AssemblyStepStateMachine,
    StepAction,
    StepProcessState,
)


class FakeVision:
    def __init__(self) -> None:
        now = datetime.now().astimezone()
        self.block = VisionMeasurement(Color.RED, EntityKind.BLOCK, 1, 2, 3, "#1;2;3", now)
        self.tray = VisionMeasurement(Color.BLUE, EntityKind.TRAY, 4, 5, 6, "#4;5;6", now)
        self.calls = []

    def locate_block(self, step):
        self.calls.append("locate_block")
        return self.block

    def locate_tray(self, step):
        self.calls.append("locate_tray")
        return self.tray


class FakeRobot:
    def __init__(self) -> None:
        self.calls = []

    def pick(self, step, measurement):
        self.calls.append(("pick", measurement))

    def move_block_photo(self):
        self.calls.append(("move_block_photo",))

    def move_tray_photo(self):
        self.calls.append(("move_tray_photo",))

    def place(self, step, measurement):
        self.calls.append(("place", measurement))

    def stack(self, step):
        self.calls.append(("stack", step.destination_color))

    def return_photo(self):
        self.calls.append(("return_photo",))


class RobotProcessTests(unittest.TestCase):
    def test_independent_actions_follow_configured_state_sequence(self) -> None:
        vision = FakeVision()
        robot = FakeRobot()
        actions = (
            StepAction.LOCATE_TRAY,
            StepAction.LOCATE_BLOCK,
            StepAction.PICK,
            StepAction.PLACE,
            StepAction.RETURN_PHOTO,
        )
        machine = AssemblyStepStateMachine(vision, robot, lambda text: None, actions)

        result = machine.run(AssemblyStep.from_colors(1, Color.RED, Color.BLUE))

        self.assertEqual(result.state, StepProcessState.COMPLETED)
        self.assertEqual(vision.calls, ["locate_tray", "locate_block"])
        self.assertEqual(
            [call[0] for call in robot.calls],
            [
                "move_tray_photo",
                "move_block_photo",
                "pick",
                "place",
                "return_photo",
            ],
        )

    def test_dependency_failure_is_recorded(self) -> None:
        machine = AssemblyStepStateMachine(
            FakeVision(),
            FakeRobot(),
            lambda text: None,
            (StepAction.PICK,),
        )
        with self.assertRaisesRegex(RuntimeError, "没有物块定位"):
            machine.run(AssemblyStep.from_colors(1, Color.RED, Color.BLUE))
        self.assertEqual(machine.snapshot.state, StepProcessState.FAILED)

    def test_seventh_step_locates_only_source_then_uses_saved_stack_pose(self) -> None:
        vision = FakeVision()
        robot = FakeRobot()
        machine = AssemblyStepStateMachine(
            vision,
            robot,
            lambda text: None,
            (
                StepAction.LOCATE_BLOCK,
                StepAction.PICK,
                StepAction.LOCATE_TRAY,
                StepAction.PLACE,
                StepAction.RETURN_PHOTO,
            ),
        )

        result = machine.run(AssemblyStep.from_stack(7, Color.PINK, Color.RED))

        self.assertEqual(result.state, StepProcessState.COMPLETED)
        self.assertEqual(vision.calls, ["locate_block"])
        self.assertEqual(
            [call[0] for call in robot.calls],
            ["move_block_photo", "pick", "stack"],
        )


if __name__ == "__main__":
    unittest.main()
