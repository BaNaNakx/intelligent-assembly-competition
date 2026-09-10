from __future__ import annotations

import unittest

from alignment_test import (
    AlignmentMeasurement,
    AlignmentTestConfig,
    ArcsMotionSafetyStatus,
    CameraAlignmentTest,
    build_camera_target,
    parse_alignment_response,
    build_suction_target,
    measurement_request,
)


class FakeVm:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[str] = []

    def request_payload(self, request: str) -> bytes:
        self.requests.append(request)
        return self.payload


class FakeArcs:
    def __init__(
        self,
        safety: ArcsMotionSafetyStatus | None = None,
        tool_pose=(0.4, -0.2, 0.428, -3.139, 0.001, -0.25),
    ) -> None:
        self.safety = safety or ArcsMotionSafetyStatus(True, True, True, False)
        self.tool_pose = tool_pose
        self.moves: list[tuple[list[float], float, float]] = []
        self.waits: list[tuple[float, float]] = []
        self.io = []
        self.stopped = False

    def get_motion_safety_status(self):
        return self.safety

    def get_tcp_pose(self):
        return self.tool_pose

    def move_line(self, pose, *, acceleration_m_s2, velocity_m_s):
        self.moves.append((pose, acceleration_m_s2, velocity_m_s))
        return 0

    def set_tool_digital_output(self, index, value):
        self.io.append((index, value))
        return 0

    def emergency_stop(self):
        self.stopped = True
        return 0

    def wait_until_steady(self, *, timeout_s, poll_interval_s):
        self.waits.append((timeout_s, poll_interval_s))


class AlignmentProgramTests(unittest.TestCase):
    def test_parses_exact_hash_zero_response(self) -> None:
        measurement = parse_alignment_response(b"#0;169.41;-394.49;-15.5;")

        self.assertEqual(
            measurement,
            AlignmentMeasurement(169.41, -394.49, -15.5, "#0;169.41;-394.49;-15.5;"),
        )

    def test_rejects_non_clean_or_malformed_response(self) -> None:
        with self.assertRaises(ValueError):
            parse_alignment_response(b"b'0;1;2;3;'")
        with self.assertRaises(ValueError):
            parse_alignment_response(b"0;1;2")
        with self.assertRaises(ValueError):
            parse_alignment_response(b"0;1;2;3;")
        with self.assertRaises(ValueError):
            parse_alignment_response(b"#0;1;2;3")

    def test_builds_camera_pose_from_vm_xy_and_rz(self) -> None:
        target = build_camera_target(
            AlignmentMeasurement(20.0, -40.0, 30.0, "#0;20;-40;30;"),
            (0.4, -0.2, 0.428, -3.139, 0.001, -0.25),
            AlignmentTestConfig(
                vm_xy_scale_k=0.5,
                rz_sign=-1.0,
                rz_offset_deg=10.0,
            ),
        )

        self.assertAlmostEqual(target[0], 0.410)
        self.assertAlmostEqual(target[1], -0.220)
        self.assertAlmostEqual(target[2], 0.428)
        self.assertAlmostEqual(target[5], -0.5990658504)

    def test_sends_exact_trigger_and_moves_only_after_safety_check(self) -> None:
        vm = FakeVm(b"#0;10;20;20;")
        arcs = FakeArcs()
        result = CameraAlignmentTest(
            AlignmentTestConfig(), vm_client=vm, arcs_client=arcs, report=lambda _: None
        ).run()

        self.assertEqual(vm.requests, ["wukuai,11"])
        self.assertEqual(len(arcs.moves), 2)
        self.assertEqual(arcs.moves[1][0], list(result.target_pose_m_rad))
        self.assertAlmostEqual(result.target_pose_m_rad[0], 0.39647)
        self.assertAlmostEqual(result.target_pose_m_rad[1], -0.30219)
        self.assertEqual(arcs.waits, [(60.0, 0.1)] * 2)

    def test_blocks_motion_when_robot_is_not_ready(self) -> None:
        vm = FakeVm(b"#0;169.41;-394.49;20;")
        arcs = FakeArcs(ArcsMotionSafetyStatus(False, True, True, False))

        with self.assertRaisesRegex(RuntimeError, "尚未上电"):
            CameraAlignmentTest(
                AlignmentTestConfig(), vm_client=vm, arcs_client=arcs, report=lambda _: None
            ).run()

        self.assertEqual(arcs.moves, [])

    def test_pick_and_place_heights_delta_and_lift(self):
        for action, trigger, height, enabled in (("pick", 11, 186, True), ("place", 23, 182, False)):
            vm, arcs = FakeVm(b"#0;10;20;0;"), FakeArcs()
            program = CameraAlignmentTest(AlignmentTestConfig(trigger=trigger, suction_settle_s=0, release_settle_s=0),
                                          vm_client=vm, arcs_client=arcs, report=lambda _: None)
            program.run(action)
            self.assertEqual(vm.requests, ["wukuai,11" if trigger == 11 else "tuopan,23"])
            self.assertEqual(len(arcs.moves), 4)
            self.assertAlmostEqual(arcs.moves[0][0][2], 0.428)
            self.assertAlmostEqual(arcs.moves[1][0][0], 0.39647)
            self.assertAlmostEqual(arcs.moves[1][0][1], -0.30219)
            self.assertAlmostEqual(arcs.moves[2][0][2], height / 1000)
            self.assertAlmostEqual(arcs.moves[3][0][2], (height + 100) / 1000)
            self.assertEqual(arcs.io, [(1, enabled)])

    def test_capture_height_and_orientation_are_preserved(self):
        config = AlignmentTestConfig()
        pose = (0.2, -0.3, 0.5, -3.1, 0.02, 0.3)
        camera = build_camera_target(AlignmentMeasurement(10, 20, 0, ""), pose, config)
        cup = build_suction_target(camera, config)
        self.assertEqual(camera[2:], pose[2:])
        self.assertEqual(cup[2:], pose[2:])

    def test_protocol_all_colors_and_invalid_codes(self):
        for code in range(11, 17):
            self.assertEqual(measurement_request(code), f"wukuai,{code}")
        for code in range(21, 27):
            self.assertEqual(measurement_request(code), f"tuopan,{code}")
        with self.assertRaises(ValueError):
            measurement_request(17)

    def test_cancel_prevents_all_motion_and_io(self):
        arcs = FakeArcs()
        program = CameraAlignmentTest(AlignmentTestConfig(), vm_client=FakeVm(b"#0;10;20;0;"),
                                      arcs_client=arcs, report=lambda _: None)
        program.cancelled.set()
        with self.assertRaises(RuntimeError):
            program.run("pick")
        self.assertEqual(arcs.moves, [])
        self.assertEqual(arcs.io, [])

    def test_memory_round_trip_and_stack_has_no_effect(self):
        from dataclasses import replace
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from run_alignment_test import load_config, save_config
        config = replace(AlignmentTestConfig(), camera_tcp_x_mm=123, stack_z_mm=500, trigger=26)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            save_config(path, config)
            self.assertEqual(load_config(path), config)
        camera = (0.1, -0.1, 0.4, 0, 0, 0)
        self.assertEqual(build_suction_target(camera, config),
                         build_suction_target(camera, replace(config, stack_z_mm=208.3)))

    def test_gui_has_calibration_and_persistent_editable_values(self):
        import tkinter as tk
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from run_alignment_test import AlignmentTestApp, load_config, PARAMETER_GROUPS
        from dataclasses import fields
        root = tk.Tk()
        root.withdraw()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            app = AlignmentTestApp(root, path)
            shown = {name for _, definitions in PARAMETER_GROUPS for name, _ in definitions}
            self.assertEqual(shown, {field.name for field in fields(AlignmentTestConfig)})
            app.values["suction_tcp_x_mm"].set("20")
            app.values["camera_tcp_x_mm"].set("7")
            app.apply()
            self.assertIn("X=13.000 mm", app.delta.get())
            self.assertEqual(load_config(path).suction_tcp_x_mm, 20)
            root.update_idletasks()
        root.destroy()

    def test_delta_outside_workspace_blocks_before_first_move(self):
        vm = FakeVm(b"#0;169.41;-394.49;20;")
        arcs = FakeArcs()
        program = CameraAlignmentTest(AlignmentTestConfig(), vm_client=vm, arcs_client=arcs, report=lambda _: None)
        with self.assertRaisesRegex(ValueError, "臂展"):
            program.run()
        self.assertEqual(arcs.moves, [])


if __name__ == "__main__":
    unittest.main()
