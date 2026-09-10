from __future__ import annotations

import unittest

from assembly.offline_devices import OFFLINE_DEVICE_PROFILE


class OfflineDeviceProfileTests(unittest.TestCase):
    def test_confirmed_models_are_fixed(self) -> None:
        self.assertEqual(OFFLINE_DEVICE_PROFILE.robot_model, "AUBO ES5")
        self.assertEqual(OFFLINE_DEVICE_PROFILE.edge_controller_model, "AUBO ERC")
        self.assertEqual(OFFLINE_DEVICE_PROFILE.camera_connection, "VisionMaster TCP/IP")


if __name__ == "__main__":
    unittest.main()
