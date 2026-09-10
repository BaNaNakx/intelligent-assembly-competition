from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime
from math import radians
from threading import Event

from assembly.arcs_jsonrpc import ArcsMotionSafetyStatus
from assembly.models import AssemblyStep, Color, EntityKind, VisionMeasurement
from assembly.offline_motion import (
    PhysicalAssemblyExecutor, PhysicalAssemblyGeometry, PhysicalAssemblyMotionPlanner,
    PhysicalMotionError, PhysicalMotionExecutionConfig, CartesianWorkspace,
)
from assembly.robot_parameters import RobotGlobalParameters, RobotParameterStore


ORIGIN = (0.4, -0.2, 0.428, -3.139, 0.001, -0.25)
STEP = AssemblyStep.from_colors(1, Color.RED, Color.BLUE)


def measurement(color=Color.RED, kind=EntityKind.BLOCK):
    return VisionMeasurement(color, kind, 10, 20, 5, "#0;10;20;5;", datetime.now().astimezone())


class FakeArcs:
    def __init__(self):
        self.calls = []
        self.tool_pose = ORIGIN
        self.after_line = lambda: None

    def get_motion_safety_status(self):
        return ArcsMotionSafetyStatus(True, True, True, False)

    def get_tcp_pose(self):
        self.calls.append(("tcp",))
        return self.tool_pose

    def move_joint(self, joints, **kwargs):
        self.calls.append(("joint", tuple(joints), kwargs))
        return 0

    def move_line(self, pose, **kwargs):
        self.calls.append(("line", tuple(pose), kwargs))
        self.tool_pose = tuple(pose)
        self.after_line()
        return 0

    def wait_until_steady(self, **kwargs):
        self.calls.append(("wait",))

    def emergency_stop(self):
        self.calls.append(("stop",))
        return 0


class FakeIo:
    def __init__(self):
        self.calls = []

    def set_suction(self, enabled):
        self.calls.append(enabled)


def executor(arcs=None, io=None, store=None, check=lambda: None, observer=lambda _record: None):
    store = store or RobotParameterStore()
    return PhysicalAssemblyExecutor(
        arcs or FakeArcs(), io or FakeIo(),
        PhysicalAssemblyMotionPlanner(PhysicalAssemblyGeometry.from_parameters(store.snapshot())),
        PhysicalMotionExecutionConfig.from_parameters(store.snapshot()), store,
        check_cancelled=check,
        placement_pose_observer=observer,
    )


class OfflineMotionTests(unittest.TestCase):
    def test_document_defaults_and_unit_conversion(self):
        parameters = RobotGlobalParameters()
        geometry = PhysicalAssemblyGeometry.from_parameters(parameters)
        self.assertEqual(geometry.pickup_z_m, 0.186)
        self.assertEqual(geometry.place_z_m, 0.182)
        self.assertEqual(parameters.stack_z_mm, 208.3)
        self.assertAlmostEqual(geometry.tcp_delta_xy_m[0], -0.01353)
        self.assertAlmostEqual(geometry.tcp_delta_xy_m[1], -0.12219)
        self.assertAlmostEqual(parameters.joint_velocity_rad_s, radians(60))
        self.assertAlmostEqual(parameters.joint_acceleration_rad_s2, radians(30))

    def test_camera_then_delta_then_vertical_pick(self):
        params = replace(RobotGlobalParameters(), vm_xy_scale_k=0.5)
        planner = PhysicalAssemblyMotionPlanner(PhysicalAssemblyGeometry.from_parameters(params))
        commands = planner.plan_pick(measurement(), ORIGIN)
        self.assertEqual([c.label for c in commands],
                         ["pick_approach", "pick_tcp_align", "pick_arrive", "suction_on", "pick_retreat"])
        camera, cup, down, _, up = commands
        self.assertAlmostEqual(camera.pose.x_m, 0.405)
        self.assertAlmostEqual(camera.pose.y_m, -0.190)
        self.assertAlmostEqual(camera.pose.rz_rad, ORIGIN[5] + radians(5))
        self.assertEqual(camera.pose.rx_rad, ORIGIN[3])
        self.assertEqual(camera.pose.ry_rad, ORIGIN[4])
        self.assertAlmostEqual(cup.pose.x_m, 0.39147)
        self.assertAlmostEqual(cup.pose.y_m, -0.31219)
        self.assertEqual(camera.pose.z_m, ORIGIN[2])
        self.assertEqual(cup.pose.z_m, ORIGIN[2])
        self.assertEqual(cup.pose.as_list()[:2], down.pose.as_list()[:2])
        self.assertEqual(down.pose.z_m, 0.186)
        self.assertAlmostEqual(up.pose.z_m, 0.286)

    def test_place_applies_delta_and_uses_separate_height(self):
        planner = PhysicalAssemblyMotionPlanner(PhysicalAssemblyGeometry.from_parameters(RobotGlobalParameters()))
        for color in Color:
            commands = planner.plan_place(measurement(Color.BLUE, EntityKind.TRAY), color, ORIGIN)
            self.assertAlmostEqual(commands[1].pose.x_m, 0.39647)
            self.assertAlmostEqual(commands[1].pose.y_m, -0.30219)
            self.assertEqual(commands[2].pose.z_m, 0.182)
            self.assertEqual(commands[3].label, "suction_off")
            self.assertAlmostEqual(commands[4].pose.z_m, 0.282)

    def test_stack_value_does_not_change_motion(self):
        first = PhysicalAssemblyMotionPlanner(PhysicalAssemblyGeometry.from_parameters(RobotGlobalParameters()))
        second = PhysicalAssemblyMotionPlanner(PhysicalAssemblyGeometry.from_parameters(
            replace(RobotGlobalParameters(), stack_z_mm=500)))
        self.assertEqual(first.plan_place(measurement(), Color.RED, ORIGIN),
                         second.plan_place(measurement(), Color.RED, ORIGIN))

    def test_records_pose1_pose2_and_stacks_without_vm_destination(self):
        arcs, io, records = FakeArcs(), FakeIo(), []
        ex = executor(arcs, io, observer=records.append)
        ex.place(STEP, measurement(Color.BLUE, EntityKind.TRAY))
        record = ex.placement_pose(Color.RED)
        self.assertEqual(records, [record])
        self.assertAlmostEqual(record.pose1.z_m, 0.182)
        self.assertAlmostEqual(record.pose2.z_m, 0.282)

        ex.stack(AssemblyStep.from_stack(7, Color.PINK, Color.RED))
        stack_lines = [call[1] for call in arcs.calls if call[0] == "line"][-3:]
        self.assertEqual(stack_lines[0], tuple(record.pose2.as_list()))
        self.assertAlmostEqual(stack_lines[1][2], 0.2083)
        self.assertAlmostEqual(stack_lines[2][2], 0.3083)
        self.assertEqual(io.calls, [False, False])
        self.assertEqual(
            [call[1] for call in arcs.calls if call[0] == "joint"][-1],
            RobotGlobalParameters().tray_photo_joints_rad,
        )

    def test_requires_live_origin_and_rejects_low_or_outside_motion(self):
        planner = PhysicalAssemblyMotionPlanner(PhysicalAssemblyGeometry.from_parameters(RobotGlobalParameters()))
        for origin in (None, (0.4, -0.2, 0.1, 0, 0, 0), (2, 0, 0.428, 0, 0, 0)):
            with self.assertRaises(PhysicalMotionError):
                planner.plan_pick(measurement(), origin)

    def test_prepare_neither_homes_nor_releases_suction(self):
        arcs, io = FakeArcs(), FakeIo()
        executor(arcs, io).prepare_round()
        self.assertEqual(arcs.calls, [])
        self.assertEqual(io.calls, [])

    def test_executor_separates_alignment_descent_io_and_lift(self):
        arcs, io = FakeArcs(), FakeIo()
        ex = executor(arcs, io)
        ex.pick(STEP, measurement())
        ex.place(STEP, measurement(Color.BLUE, EntityKind.TRAY))
        ex.return_photo()
        lines = [call for call in arcs.calls if call[0] == "line"]
        self.assertEqual(len(lines), 8)
        self.assertEqual(io.calls, [True, False])
        self.assertEqual(lines[0][2]["velocity_m_s"], 0.4)
        self.assertEqual(lines[2][2]["velocity_m_s"], 0.3)
        self.assertEqual(lines[6][2]["velocity_m_s"], 0.3)
        self.assertEqual([call for call in arcs.calls if call[0] == "joint"][0][1],
                         RobotGlobalParameters().block_photo_joints_rad)

    def test_three_photo_positions_and_latest_saved_joints(self):
        arcs, store = FakeArcs(), RobotParameterStore()
        store.update_from_form({"task_card_photo_j1_deg": "25"})
        ex = executor(arcs, store=store)
        ex.move_task_card_photo(1)
        ex.move_task_card_photo(2)
        ex.move_block_photo()
        ex.move_tray_photo()
        joints = [call[1] for call in arcs.calls if call[0] == "joint"]
        self.assertEqual(joints[0], joints[1])
        self.assertAlmostEqual(joints[0][0], radians(25))
        self.assertEqual(joints[2], store.snapshot().block_photo_joints_rad)
        self.assertEqual(joints[3], store.snapshot().tray_photo_joints_rad)

    def test_lift_uses_actual_pose_after_io_for_both_pick_and_place(self):
        for action in ("pick", "place"):
            with self.subTest(action=action):
                arcs, io = FakeArcs(), FakeIo()
                actual = (0.411, -0.31, 0.195, -3.138, 0.002, -0.11)
                def set_suction(enabled):
                    io.calls.append(enabled)
                    arcs.tool_pose = actual
                io.set_suction = set_suction
                getattr(executor(arcs, io), action)(STEP, measurement())
                last = [c for c in arcs.calls if c[0] == "line"][-1][1]
                self.assertEqual(last[:2], actual[:2])
                self.assertEqual(last[3:], actual[3:])
                self.assertAlmostEqual(last[2], 0.295)
                expected_tcp_reads = 2 if action == "pick" else 3
                self.assertEqual(len([c for c in arcs.calls if c[0] == "tcp"]), expected_tcp_reads)

    def test_actual_lift_outside_workspace_is_blocked_after_io(self):
        arcs, io = FakeArcs(), FakeIo()
        io.set_suction = lambda _: setattr(arcs, "tool_pose", (0.1, 0.1, 0.85, 0, 0, 0))
        with self.assertRaises(PhysicalMotionError):
            executor(arcs, io).pick(STEP, measurement())
        self.assertEqual(len([c for c in arcs.calls if c[0] == "line"]), 3)
        self.assertIn(("stop",), arcs.calls)

    def test_speed_updates_next_command_geometry_updates_next_action(self):
        arcs, store = FakeArcs(), RobotParameterStore()
        ex = executor(arcs, store=store)
        arcs.after_line = lambda: store.update_from_form({"precision_velocity_m_s": "0.02", "block_place_z_mm": "200"})
        ex.pick(STEP, measurement())
        ex.place(STEP, measurement())
        lines = [call for call in arcs.calls if call[0] == "line"]
        self.assertEqual(lines[2][2]["velocity_m_s"], 0.02)
        self.assertEqual(lines[6][1][2], 0.2)

    def test_cancel_blocks_later_commands_without_releasing_block(self):
        arcs, io, cancelled = FakeArcs(), FakeIo(), Event()
        def check():
            if cancelled.is_set():
                raise RuntimeError("cancelled")
        ex = executor(arcs, io, check=check)
        arcs.after_line = cancelled.set
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            ex.pick(STEP, measurement())
        self.assertEqual(len([c for c in arcs.calls if c[0] == "line"]), 1)
        self.assertEqual(io.calls, [])
        self.assertIn(("stop",), arcs.calls)
