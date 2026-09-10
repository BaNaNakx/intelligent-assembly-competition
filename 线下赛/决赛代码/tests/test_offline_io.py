from __future__ import annotations

import unittest

from assembly.offline_io import ArcsToolSuctionIo, ToolSuctionConfig
from assembly.robot_parameters import RobotParameterStore


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def set_tool_digital_output(self, index, value):
        self.calls.append((index, value))
        return 0


class OfflineIoTests(unittest.TestCase):
    def test_tool_one_sucks_and_tool_zero_pulses_blow_for_release(self) -> None:
        client = FakeClient()
        delays = []
        io = ArcsToolSuctionIo(client, sleep=delays.append)

        io.set_suction(True)
        io.set_suction(False)

        self.assertEqual(
            client.calls,
            [
                (0, False),
                (1, True),
                (1, False),
                (0, True),
                (0, False),
            ],
        )
        self.assertEqual(delays, [0.20, 0.20])

    def test_rejects_nonexistent_tool_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "0到3"):
            ToolSuctionConfig(output_index=4)
        with self.assertRaisesRegex(ValueError, "不同"):
            ToolSuctionConfig(output_index=1, blow_output_index=1)

    def test_hot_io_parameters_apply_to_next_switch(self) -> None:
        port = FakeClient()
        store = RobotParameterStore()
        io = ArcsToolSuctionIo(port, sleep=lambda seconds: None, parameter_store=store)

        io.set_suction(True)
        store.update_from_form({
            "tool_do_index": "2",
            "tool_blow_do_index": "3",
            "tool_do_active_high": "false",
        })
        io.set_suction(True)

        self.assertEqual(
            port.calls,
            [(0, False), (1, True), (3, True), (2, False)],
        )


if __name__ == "__main__":
    unittest.main()
