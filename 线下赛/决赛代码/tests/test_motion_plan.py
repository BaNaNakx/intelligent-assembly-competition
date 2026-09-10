from __future__ import annotations

import unittest
from math import pi

from assembly.models import AssemblyStep, Color, EntityKind
from assembly.motion_plan import (
    INITIAL_JOINTS_RAD,
    MotionKind,
    VirtualAssemblyMotionPlanner,
    VmToArcsCoordinateTransformer,
)
from assembly.vm_protocol import VisionMeasurementStore, fixed_vm_packets


class MotionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        store = VisionMeasurementStore()
        for packet in fixed_vm_packets():
            store.ingest(packet)
        self.measurements = store.snapshot()

    def test_converts_vm_millimetres_to_arcs_metres_one_to_one(self) -> None:
        transformer = VmToArcsCoordinateTransformer()
        red_block = transformer.to_task_pose(self.measurements[(Color.RED, EntityKind.BLOCK)])
        purple_tray = transformer.to_task_pose(
            self.measurements[(Color.PURPLE, EntityKind.TRAY)]
        )

        self.assertEqual((red_block.x_m, red_block.y_m, red_block.z_m), (0.4795, -0.1225, 0.01))
        self.assertAlmostEqual(red_block.rz_rad, pi / 2 + pi / 18)
        self.assertAlmostEqual(purple_tray.x_m, 0.5385)
        self.assertAlmostEqual(purple_tray.y_m, -0.1815)
        self.assertEqual(purple_tray.z_m, 0.01)
        self.assertAlmostEqual(purple_tray.rz_rad, pi / 2 - pi / 3)

    def test_every_pick_place_step_returns_to_initial_joint_pose(self) -> None:
        step = AssemblyStep.from_colors(1, Color.RED, Color.YELLOW)
        commands = VirtualAssemblyMotionPlanner().plan_step(step, self.measurements)

        self.assertEqual(
            [command.label for command in commands],
            [
                "pick_approach",
                "pick_arrive",
                "pick_retreat",
                "place_approach",
                "place_arrive",
                "place_retreat",
                "return_initial_pose",
            ],
        )
        self.assertTrue(all(command.kind is MotionKind.MOVE_LINE for command in commands[:6]))
        self.assertEqual(commands[0].pose.z_m, 0.12)
        self.assertEqual(commands[1].pose.z_m, 0.01)
        self.assertEqual(commands[-1].kind, MotionKind.MOVE_JOINT)
        self.assertEqual(commands[-1].joints_rad, INITIAL_JOINTS_RAD)

    def test_rejects_missing_vm_coordinate(self) -> None:
        step = AssemblyStep.from_colors(1, Color.RED, Color.YELLOW)
        missing = dict(self.measurements)
        del missing[(Color.YELLOW, EntityKind.TRAY)]

        with self.assertRaises(ValueError):
            VirtualAssemblyMotionPlanner().plan_step(step, missing)
