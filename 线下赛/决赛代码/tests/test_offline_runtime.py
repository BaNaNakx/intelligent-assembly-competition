from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from assembly.competition_runtime import OfflineCompetitionRunner
from assembly.offline_motion import PhysicalAssemblyExecutor
from assembly.offline_workflow import OfflineRoundWorkflow
from assembly.offline_launch_settings import OfflineLaunchSettings
from assembly.settings import load_competition_config
from assembly.robot_parameters import RobotParameterStore


class OfflineRuntimeTests(unittest.TestCase):
    def test_one_click_runner_wires_direct_vision_tool_do_motion_and_telemetry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = load_competition_config(root / "config" / "competition_config.toml")
        settings = OfflineLaunchSettings(
            "key", "127.0.0.1", 7930, "192.168.1.12", 30004,
        )
        store = RobotParameterStore()
        with TemporaryDirectory() as directory:
            session = OfflineCompetitionRunner(
                settings,
                settings.apply_to(template),
                Path(directory),
                lambda text: None,
                store,
            ).create_session()

        self.assertIsInstance(session.workflow, OfflineRoundWorkflow)
        self.assertIsInstance(session.workflow._robot._executor, PhysicalAssemblyExecutor)
        self.assertEqual(
            session.robot_parameters.snapshot().tool_do_index,
            1,
        )
        self.assertIsNotNone(session.read_robot_snapshot)
        self.assertIsNotNone(session.read_motion_status)


if __name__ == "__main__":
    unittest.main()
