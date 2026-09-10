from __future__ import annotations

import unittest

from assembly.models import Color
from assembly.robot_parameters import (
    DEFAULT_STEP_ACTIONS,
    ROBOT_GLOBAL_PARAMETERS,
    RobotGlobalParameters,
    RobotParameterStore,
)


class RobotParameterTests(unittest.TestCase):
    def test_confirmed_robot_test_defaults(self) -> None:
        parameters = ROBOT_GLOBAL_PARAMETERS
        self.assertEqual(parameters.task_sequence, ("task1", "task2"))
        self.assertEqual(
            tuple(parameters.block_height_mm(color) for color in Color),
            (186.0,) * len(Color),
        )
        self.assertEqual(parameters.lift_distance_mm, 100.0)
        self.assertEqual(parameters.vm_xy_scale_k, 1.0)
        self.assertEqual(parameters.block_pick_z_mm, 186.0)
        self.assertEqual(
            (
                parameters.suction_tcp_x_mm,
                parameters.suction_tcp_y_mm,
            ),
            (-27.50, -624.48),
        )
        self.assertEqual(parameters.photo_settle_s, 1.0)
        self.assertEqual(parameters.model_result_settle_s, 1.0)
        self.assertEqual(parameters.step_action_sequence, DEFAULT_STEP_ACTIONS)
        self.assertEqual(parameters.tool_do_index, 1)
        self.assertEqual(parameters.tool_blow_do_index, 0)
        self.assertTrue(parameters.tool_do_active_high)
        self.assertEqual(
            parameters.block_photo_joints_deg,
            (-35.89, 8.39, 109.55, 10.89, 90.0, 55.45),
        )
        self.assertEqual(
            parameters.tray_photo_joints_deg,
            (-83.90, 16.77, 115.91, 8.87, 89.87, 7.43),
        )
        self.assertEqual(
            parameters.task_card_slot1_joints_deg,
            (-84.46, -14.81, 84.92, 9.45, 89.92, 6.87),
        )
        self.assertEqual(
            parameters.task_card_slot2_joints_deg,
            (-84.46, -14.81, 84.92, 9.45, 89.92, 6.87),
        )
        self.assertEqual(
            (
                parameters.block_photo_pose.x_mm,
                parameters.block_photo_pose.y_mm,
                parameters.block_photo_pose.z_mm,
                parameters.block_photo_pose.rx_rad,
                parameters.block_photo_pose.ry_rad,
                parameters.block_photo_pose.rz_rad,
            ),
            (169.41, -394.49, 428.00, -3.139, 0.001, -0.277),
        )
        self.assertEqual(
            (
                parameters.tray_photo_pose.x_mm,
                parameters.tray_photo_pose.y_mm,
                parameters.tray_photo_pose.z_mm,
                parameters.tray_photo_pose.rx_rad,
                parameters.tray_photo_pose.ry_rad,
                parameters.tray_photo_pose.rz_rad,
            ),
            (-171.95, -334.41, 428.02, -3.139, 0.001, -0.276),
        )
        self.assertEqual(
            (
                parameters.task_card_photo_pose.x_mm,
                parameters.task_card_photo_pose.y_mm,
                parameters.task_card_photo_pose.z_mm,
                parameters.task_card_photo_pose.rx_rad,
                parameters.task_card_photo_pose.ry_rad,
                parameters.task_card_photo_pose.rz_rad,
            ),
            (-209.36, -551.80, 428.04, -3.139, 0.001, -0.276),
        )
        self.assertEqual(
            (
                parameters.task_card_photo_pose.x_mm,
                parameters.task_card_photo_pose.y_mm,
                parameters.task_card_photo_pose.z_mm,
                parameters.task_card_photo_pose.rx_rad,
                parameters.task_card_photo_pose.ry_rad,
                parameters.task_card_photo_pose.rz_rad,
            ),
            (-209.36, -551.80, 428.04, -3.139, 0.001, -0.276),
        )

    def test_form_round_trip_preserves_all_parameters(self) -> None:
        values = ROBOT_GLOBAL_PARAMETERS.to_form_values()
        self.assertEqual(RobotGlobalParameters.from_form(values), ROBOT_GLOBAL_PARAMETERS)

    def test_parameter_store_applies_valid_changes_atomically(self) -> None:
        store = RobotParameterStore()
        updated = store.update_from_form(
            {
                "transit_velocity_m_s": "0.25",
                "vm_xy_scale_k": "0.8",
                "lift_distance_mm": "430",
                "block_pick_z_mm": "260",
                "suction_tcp_x_mm": "172.5",
            }
        )
        self.assertEqual(updated.transit_velocity_m_s, 0.25)
        self.assertEqual(updated.vm_xy_scale_k, 0.8)
        self.assertEqual(updated.block_pick_z_mm, 260.0)
        self.assertEqual(updated.suction_tcp_x_mm, 172.5)
        self.assertEqual(store.snapshot().lift_distance_mm, 430.0)

    def test_parameter_store_updates_tcp_and_individual_photo_joint_fields(self) -> None:
        store = RobotParameterStore()

        updated = store.update_from_form(
            {
                "tray_photo_x_mm": "-172.5",
                "tray_photo_rz_rad": "-0.28",
                "tray_photo_j2_deg": "17.25",
            }
        )

        self.assertEqual(updated.tray_photo_pose.x_mm, -172.5)
        self.assertEqual(updated.tray_photo_pose.rz_rad, -0.28)
        self.assertEqual(updated.tray_photo_pose.joints_deg[1], 17.25)

    def test_invalid_update_leaves_previous_snapshot_unchanged(self) -> None:
        store = RobotParameterStore()
        before = store.snapshot()
        with self.assertRaises(ValueError):
            store.update_from_form({"lift_distance_mm": "-1"})
        self.assertEqual(store.snapshot(), before)

    def test_state_sequence_enforces_dependencies(self) -> None:
        values = ROBOT_GLOBAL_PARAMETERS.to_form_values()
        values["step_action_sequence"] = (
            "pick,locate_block,locate_tray,place,return_photo"
        )
        with self.assertRaisesRegex(ValueError, "先定位物块"):
            RobotGlobalParameters.from_form(values)


if __name__ == "__main__":
    unittest.main()
