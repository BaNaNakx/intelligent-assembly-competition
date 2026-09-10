from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assembly.local_settings import LocalSettingsStore, RememberedConnection
from assembly.robot_parameters import RobotGlobalParameters


class LocalSettingsTests(unittest.TestCase):
    def test_missing_file_returns_script_defaults_without_api_key(self) -> None:
        with TemporaryDirectory() as directory:
            defaults = RememberedConnection("127.0.0.1", 7930, "192.168.1.12", 30004)
            loaded = LocalSettingsStore(Path(directory) / "settings.json").load(
                defaults,
                RobotGlobalParameters(),
            )

        self.assertEqual(loaded.connection, defaults)
        self.assertEqual(loaded.robot_parameters.lift_distance_mm, 100.0)

    def test_round_trip_remembers_connection_and_all_robot_parameters(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = LocalSettingsStore(path)
            connection = RememberedConnection(
                "192.168.1.20",
                9000,
                "192.168.1.30",
                30004,
                "C:/VM/cards",
            )
            values = RobotGlobalParameters(
                block_pick_z_mm=260,
                suction_tcp_x_mm=171.5,
                suction_tcp_y_mm=-392.5,
                lift_distance_mm=430,
            ).to_form_values()
            values["block_photo_x_mm"] = "170.25"
            values["block_photo_j1_deg"] = "-36.5"
            values["task_card_photo_rz_rad"] = "-0.281"
            values["task_card_photo_j6_deg"] = "38.5"
            robot = RobotGlobalParameters.from_form(values)

            store.save(connection, robot)
            loaded = store.load(
                RememberedConnection("127.0.0.1", 7930, "127.0.0.1", 30004),
                RobotGlobalParameters(),
            )

            payload = path.read_text(encoding="utf-8")

        self.assertEqual(loaded.connection, connection)
        self.assertNotIn("task_card1_image_filename", payload)
        self.assertNotIn("task_card_capture_wait_s", payload)
        self.assertEqual(loaded.robot_parameters, robot)
        self.assertEqual(loaded.robot_parameters.block_photo_pose.x_mm, 170.25)
        self.assertEqual(loaded.robot_parameters.block_photo_pose.joints_deg[0], -36.5)
        self.assertEqual(loaded.robot_parameters.task_card_photo_pose.rz_rad, -0.281)
        self.assertEqual(loaded.robot_parameters.task_card_photo_pose.joints_deg[5], 38.5)
        self.assertEqual(loaded.robot_parameters.block_pick_z_mm, 260.0)
        self.assertEqual(loaded.robot_parameters.suction_tcp_y_mm, -392.5)
        self.assertNotIn("api_key", payload)

    def test_old_per_color_height_memory_migrates_to_shared_height(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                '{"connection":{"visionmaster_host":"vm","visionmaster_port":7930,'
                '"arcs_host":"arcs","arcs_port":30004,'
                '"task_card_image_directory":"C:/VM/task_cards",'
                '"task_card1_image_filename":"task_card_1.jpg",'
                '"task_card2_image_filename":"task_card_2.jpg",'
                '"task_card_capture_wait_s":4.0},'
                '"robot_parameters":{"red_block_z_mm":"258"}}',
                encoding="utf-8",
            )

            loaded = LocalSettingsStore(path).load(
                RememberedConnection("default-vm", 7930, "default-arcs", 30004),
                RobotGlobalParameters(),
            )

        self.assertEqual(loaded.robot_parameters.block_pick_z_mm, 186.0)

    def test_corrupt_memory_falls_back_to_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            defaults = RememberedConnection("vm", 7930, "arcs", 30004)

            loaded = LocalSettingsStore(path).load(defaults, RobotGlobalParameters())

        self.assertEqual(loaded.connection, defaults)


if __name__ == "__main__":
    unittest.main()
