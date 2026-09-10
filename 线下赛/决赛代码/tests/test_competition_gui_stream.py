import unittest

from assembly.arcs_jsonrpc import ArcsMotionSafetyStatus, ArcsRobotSnapshot
from assembly.competition_gui import (
    _format_robot_telemetry,
    _format_timed_stream_fragment,
    editable_parameter_names,
)
from assembly.robot_parameters import ROBOT_GLOBAL_PARAMETERS


class CompetitionGuiStreamTests(unittest.TestCase):
    def test_prefixes_each_nonempty_reasoning_line_once(self) -> None:
        first, line_start = _format_timed_stream_fragment(
            "1. 任务类型",
            5,
            True,
        )
        second, line_start = _format_timed_stream_fragment(
            "判断\n\n2. 画面扫描\n",
            7,
            line_start,
        )

        self.assertEqual(
            first + second,
            "[0m5s] 1. 任务类型判断\n\n[0m7s] 2. 画面扫描\n",
        )
        self.assertTrue(line_start)

    def test_continuation_fragment_does_not_repeat_timestamp(self) -> None:
        text, line_start = _format_timed_stream_fragment("继续推理", 63, False)

        self.assertEqual(text, "继续推理")
        self.assertFalse(line_start)

    def test_frontend_only_exposes_document_parameters_without_duplicates(self) -> None:
        actual = editable_parameter_names()
        self.assertEqual(len(actual), len(set(actual)))
        self.assertEqual(len(actual), 54)
        self.assertIn("stack_z_mm", actual)
        self.assertIn("suction_tcp_x_mm", actual)
        self.assertIn("task_card_photo_j6_deg", actual)
        self.assertIn("joint_velocity_deg_s", actual)
        self.assertIn("tool_do_index", actual)
        self.assertIn("tool_blow_do_index", actual)
        self.assertIn("tool_do_active_high", actual)
        for hidden in ("workspace_max_x_mm", "zero_joints_deg", "vm_xy_scale_k",
                       "step_action_sequence", "lift_distance_mm"):
            self.assertNotIn(hidden, actual)
            self.assertIn(hidden, ROBOT_GLOBAL_PARAMETERS.to_form_values())

    def test_formats_arcs_snapshot_in_degrees_and_millimetres(self) -> None:
        values = _format_robot_telemetry(
            ArcsRobotSnapshot(
                "rob1",
                (0.0, 1.5707963267948966, 0.0, 0.0, 0.0, 0.0),
                (0.4785, -0.1215, 0.254, 3.141592653589793, 0.0, 1.5707963267948966),
                ("Idle",) * 6,
                False,
            ),
            ArcsMotionSafetyStatus(True, False, True, False),
        )

        self.assertEqual(values["connection"], "ARCS 已连接：rob1")
        self.assertIn("J2=90.00°", values["joints"])
        self.assertIn("X=478.50 mm", values["pose"])
        self.assertIn("Z=254.00 mm", values["pose"])
        self.assertIn("运动中", values["motion"])


if __name__ == "__main__":
    unittest.main()
