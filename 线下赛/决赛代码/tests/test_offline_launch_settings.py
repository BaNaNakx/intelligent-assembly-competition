from __future__ import annotations

import unittest
from pathlib import Path

from assembly.offline_launch_settings import OfflineLaunchSettings
from assembly.settings import load_competition_config


class OfflineLaunchSettingsTests(unittest.TestCase):
    def test_parses_direct_vm_and_robot_addresses(self) -> None:
        settings = OfflineLaunchSettings.from_form(
            api_key="key",
            visionmaster_host="192.168.1.10",
            visionmaster_port="7930",
            arcs_host="192.168.1.12",
            arcs_port="30004",
            task_card_image_directory="C:/VM/cards",
        )

        self.assertEqual(settings.visionmaster_port, 7930)
        self.assertEqual(settings.arcs_port, 30004)
        self.assertEqual(settings.robot_and_arcs_host, settings.arcs_host)

    def test_runtime_form_overrides_vm_and_shared_robot_controller_addresses(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = load_competition_config(root / "config" / "competition_config.toml")
        settings = OfflineLaunchSettings(
            "key", "192.168.10.5", 9000, "192.168.10.12", 30004,
            "C:/VM/cards",
        )

        applied = settings.apply_to(template)

        self.assertEqual(applied.visionmaster.tcp.host, "192.168.10.5")
        self.assertEqual(applied.visionmaster.tcp.port, 9000)
        self.assertEqual(applied.visionmaster.tcp.task_card_image_directory, "C:/VM/cards")
        self.assertEqual(applied.visionmaster.tcp.task_card_capture_timeout_s, 15.0)
        self.assertEqual(applied.aubo_arcs.host, "192.168.10.12")
        self.assertEqual(applied.vmware.arcs_guest_ip, "192.168.10.12")


if __name__ == "__main__":
    unittest.main()
