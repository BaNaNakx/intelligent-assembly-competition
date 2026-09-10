from __future__ import annotations

import unittest
from pathlib import Path

from assembly.launch_settings import CompetitionLaunchSettings
from assembly.settings import load_competition_config


class CompetitionLaunchSettingsTests(unittest.TestCase):
    def test_gui_values_are_runtime_only_and_share_vmware_arcs_ip(self) -> None:
        template = load_competition_config(
            Path(__file__).parents[1] / "config" / "competition_config.toml"
        )
        settings = CompetitionLaunchSettings.from_form(
            api_key="temporary-key",
            visionmaster_host="127.0.0.1",
            visionmaster_port="7930",
            arcs_host="192.168.1.16",
            arcs_port="30004",
        )

        config = settings.apply_to(template)

        self.assertEqual(config.visionmaster.tcp.host, "127.0.0.1")
        self.assertEqual(config.visionmaster.tcp.port, 7930)
        self.assertEqual(config.vmware.arcs_guest_ip, "192.168.1.16")
        self.assertEqual(config.aubo_arcs.host, "192.168.1.16")
        self.assertEqual(config.aubo_arcs.port, 30004)
        self.assertNotIn("temporary-key", str(config))

    def test_rejects_missing_key_and_non_numeric_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "API 密钥"):
            CompetitionLaunchSettings.from_form(
                api_key="",
                visionmaster_host="127.0.0.1",
                visionmaster_port="7930",
                arcs_host="192.168.1.16",
                arcs_port="30004",
            )
        with self.assertRaisesRegex(ValueError, "端口必须"):
            CompetitionLaunchSettings.from_form(
                api_key="key",
                visionmaster_host="127.0.0.1",
                visionmaster_port="not-a-port",
                arcs_host="192.168.1.16",
                arcs_port="30004",
            )
