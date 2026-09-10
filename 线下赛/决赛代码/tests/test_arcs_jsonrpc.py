from __future__ import annotations

import unittest
from collections.abc import Mapping

from assembly.arcs_jsonrpc import (
    ArcsProtocolError,
    ArcsRemoteError,
    AuboArcsJsonRpcClient,
)
from assembly.settings import AuboArcsConnectionConfig


class FakeTransport:
    def __init__(self, results: Mapping[str, object]) -> None:
        self._results = results
        self.requests: list[Mapping[str, object]] = []

    def request(
        self,
        host: str,
        port: int,
        request: Mapping[str, object],
        timeout_s: float,
    ) -> Mapping[str, object]:
        self.requests.append(request)
        method = request["method"]
        result = self._results[method]  
        if isinstance(result, Exception):
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"message": str(result)},
            }
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def _config() -> AuboArcsConnectionConfig:
    return AuboArcsConnectionConfig(
        host="192.168.1.16",
        port=30004,
        robot_name="rob1",
        request_timeout_s=5.0,
    )


def _client(transport: FakeTransport) -> AuboArcsJsonRpcClient:
    return AuboArcsJsonRpcClient(_config(), transport=transport)


class AuboArcsJsonRpcClientTests(unittest.TestCase):
    def test_reads_validated_safety_snapshot_without_motion(self) -> None:
        transport = FakeTransport(
            {
                "getRobotNames": ["rob1"],
                "rob1.RobotState.getJointPositions": [0, 1, 2, 3, 4, 5],
                "rob1.RobotState.getTcpPose": [0.1, 0.2, 0.3, 3.14, 0, 1.57],
                "rob1.RobotState.getJointState": ["Running"] * 6,
                "rob1.RobotState.isCollisionOccurred": False,
            }
        )

        snapshot = _client(transport).get_snapshot()

        self.assertEqual(snapshot.robot_name, "rob1")
        self.assertEqual(snapshot.joint_positions_rad, (0, 1, 2, 3, 4, 5))
        self.assertFalse(snapshot.collision_occurred)
        self.assertFalse(
            any("MotionControl" in request["method"] for request in transport.requests)
        )

    def test_sends_documented_quick_stop_only_when_explicitly_called(self) -> None:
        transport = FakeTransport(
            {"rob1.MotionControl.stopMove": 0}
        )

        result = _client(transport).emergency_stop()

        self.assertEqual(result, 0)
        self.assertEqual(
            transport.requests[0]["params"],
            [True, True],
        )

    def test_reads_motion_safety_and_uses_documented_motion_parameters(self) -> None:
        transport = FakeTransport(
            {
                "rob1.RobotState.isPowerOn": True,
                "rob1.RobotState.isSteady": True,
                "rob1.RobotState.isWithinSafetyLimits": True,
                "rob1.RobotState.isCollisionOccurred": False,
                "rob1.MotionControl.moveJoint": 0,
                "rob1.MotionControl.moveLine": 0,
                "rob1.IoControl.setToolDigitalOutput": 0,
                "rob1.Trace.textmsg": 0,
            }
        )
        client = _client(transport)

        status = client.get_motion_safety_status()
        client.move_joint((0, 0, 1.57, 0, 1.57, 0), acceleration_rad_s2=0.5, velocity_rad_s=0.4)
        client.move_line(
            [0.4, -0.2, 0.03, -3.14, 0, 1.57],
            acceleration_m_s2=0.3,
            velocity_m_s=0.1,
        )
        client.set_tool_digital_output(1, True)
        client.trace_textmsg("STEP 1 VM_RX BLOCK=#0;1;-1;10")

        self.assertTrue(status.powered_on)
        self.assertFalse(status.collision_occurred)
        self.assertEqual(
            transport.requests[-4]["params"],
            [[0, 0, 1.57, 0, 1.57, 0], 0.5, 0.4, 0.0, 0.0],
        )
        self.assertEqual(
            transport.requests[-3]["params"],
            [[0.4, -0.2, 0.03, -3.14, 0, 1.57], 0.3, 0.1, 0.0, 0.0],
        )
        self.assertEqual(
            transport.requests[-2]["params"],
            [1, True],
        )
        self.assertEqual(
            transport.requests[-1]["params"],
            ["STEP 1 VM_RX BLOCK=#0;1;-1;10"],
        )

    def test_rejects_unexpected_robot_or_remote_error(self) -> None:
        client = _client(FakeTransport({"getRobotNames": ["other"]}))
        with self.assertRaises(ArcsProtocolError):
            client.get_snapshot()

        client = _client(FakeTransport({"getRobotNames": RuntimeError("denied")}))
        with self.assertRaises(ArcsRemoteError):
            client.get_robot_names()

    def test_motion_status_13_is_a_documented_ignored_short_segment(self) -> None:
        transport = FakeTransport(
            {
                "rob1.MotionControl.moveJoint": 13,
                "rob1.MotionControl.moveLine": 13,
            }
        )
        client = _client(transport)

        self.assertEqual(
            client.move_joint(
                (0, 0, 0, 0, 0, 0),
                acceleration_rad_s2=1.0,
                velocity_rad_s=0.8,
            ),
            13,
        )
        self.assertEqual(
            client.move_line(
                (0.4, -0.2, 0.3, -3.14, 0, 0),
                acceleration_m_s2=0.8,
                velocity_m_s=0.3,
            ),
            13,
        )

    def test_reads_validated_native_trace_records(self) -> None:
        transport = FakeTransport(
            {
                "rob1.Trace.peek": [
                    {
                        "timestamp": 5102883064300,
                        "level": "INFO",
                        "source": "rob1",
                        "code": 0,
                        "args": ["COMPETITION_START run_id=test_run"],
                    },
                    {
                        "timestamp": 5102884064300,
                        "level": "INFO",
                        "source": "rob1",
                        "code": 0,
                        "args": ["STEP 1 RETURN_INITIAL_TCP", "X=478.50mm"],
                    },
                ]
            }
        )

        records = _client(transport).peek_trace(max_records=10000, last_time=0)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].text, "COMPETITION_START run_id=test_run")
        self.assertIn("X=478.50mm", records[1].text)
        self.assertEqual(transport.requests[0]["params"], [10000, 0])

    def test_rejects_malformed_native_trace_record(self) -> None:
        transport = FakeTransport(
            {
                "rob1.Trace.peek": [
                    {
                        "timestamp": "bad",
                        "level": "INFO",
                        "source": "rob1",
                        "code": 0,
                        "args": ["message"],
                    }
                ]
            }
        )

        with self.assertRaisesRegex(ArcsProtocolError, "时间戳"):
            _client(transport).peek_trace()
