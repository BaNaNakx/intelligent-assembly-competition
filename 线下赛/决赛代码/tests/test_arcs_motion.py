from __future__ import annotations

import unittest

from assembly.arcs_jsonrpc import ArcsMotionSafetyStatus, ArcsRobotSnapshot
from assembly.arcs_motion import ArcsAssemblyExecutor, ArcsMotionExecutionError
from assembly.models import AssemblyStep, Color
from assembly.motion_plan import INITIAL_JOINTS_RAD
from assembly.vm_protocol import VisionMeasurementStore, fixed_vm_packets


class FakeArcsMotionClient:
    def __init__(
        self,
        *,
        powered_on: bool = True,
        collision: bool = False,
        at_initial_pose: bool = False,
    ) -> None:
        self.status = ArcsMotionSafetyStatus(
            powered_on=powered_on,
            steady=True,
            within_safety_limits=True,
            collision_occurred=collision,
        )
        self.calls: list[tuple[str, object]] = []
        self._joint_positions_rad = INITIAL_JOINTS_RAD if at_initial_pose else (0.0,) * 6

    def get_motion_safety_status(self) -> ArcsMotionSafetyStatus:
        return self.status

    def get_snapshot(self) -> ArcsRobotSnapshot:
        return ArcsRobotSnapshot(
            robot_name="rob1",
            joint_positions_rad=self._joint_positions_rad,
            tool_pose_m_rad=(0.0,) * 6,
            joint_states=("Running",) * 6,
            collision_occurred=self.status.collision_occurred,
        )

    def move_joint(self, joints_rad, *, acceleration_rad_s2, velocity_rad_s) -> int:
        self.calls.append(("move_joint", joints_rad))
        return 0

    def move_line(self, pose_m_rad, *, acceleration_m_s2, velocity_m_s) -> int:
        self.calls.append(("move_line", pose_m_rad))
        return 0

    def wait_until_steady(self, *, timeout_s, poll_interval_s) -> None:
        self.calls.append(("wait", timeout_s))

    def emergency_stop(self) -> int:
        self.calls.append(("stop", None))
        return 0

    def trace_textmsg(self, message: str) -> int:
        self.calls.append(("trace", message))
        return 0


class ArcsMotionTests(unittest.TestCase):
    def setUp(self) -> None:
        store = VisionMeasurementStore()
        for packet in fixed_vm_packets():
            store.ingest(packet)
        self.measurements = store.snapshot()
        self.step = AssemblyStep.from_colors(1, Color.RED, Color.YELLOW)

    def test_moves_to_initial_pose_then_executes_and_logs_vm_evidence(self) -> None:
        client = FakeArcsMotionClient()
        ArcsAssemblyExecutor(client).execute_step(self.step, self.measurements)

        motion_calls = [call[0] for call in client.calls if call[0].startswith("move_")]
        self.assertEqual(motion_calls, ["move_joint"] + ["move_line"] * 6 + ["move_joint"])
        self.assertEqual(sum(call[0] == "wait" for call in client.calls), 8)
        traces = [call[1] for call in client.calls if call[0] == "trace"]
        self.assertTrue(any("VM_RX BLOCK=#0;1;-1;10" in trace for trace in traces))
        self.assertTrue(any("RETURN_INITIAL_TCP X=478.50mm" in trace for trace in traces))

    def test_refuses_motion_before_power_on(self) -> None:
        client = FakeArcsMotionClient(powered_on=False)
        with self.assertRaisesRegex(ArcsMotionExecutionError, "尚未上电"):
            ArcsAssemblyExecutor(client).execute_step(self.step, self.measurements)
        self.assertEqual(client.calls, [])

    def test_skips_duplicate_initial_joint_motion_when_already_at_initial_pose(self) -> None:
        client = FakeArcsMotionClient(at_initial_pose=True)

        ArcsAssemblyExecutor(client).prepare_initial_pose()

        self.assertFalse(any(call[0] == "move_joint" for call in client.calls))
        self.assertTrue(any(call[0] == "trace" for call in client.calls))

    def test_stops_when_collision_is_reported(self) -> None:
        client = FakeArcsMotionClient(collision=True)
        with self.assertRaisesRegex(ArcsMotionExecutionError, "碰撞"):
            ArcsAssemblyExecutor(client).execute_step(self.step, self.measurements)
        self.assertEqual(client.calls, [])
