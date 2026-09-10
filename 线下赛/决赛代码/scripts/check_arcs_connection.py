'Read-only ARCS JSON-RPC connection and safety-state check.'

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from assembly.arcs_jsonrpc import AuboArcsJsonRpcClient
from assembly.settings import load_competition_config


def main() -> None:
    config = load_competition_config(
        PROJECT_ROOT / "config" / "competition_config.toml"
    )
    snapshot = AuboArcsJsonRpcClient(config.aubo_arcs).get_snapshot()
    print(f"ARCS 已连接：{snapshot.robot_name}")
    print(f"关节状态：{', '.join(snapshot.joint_states)}")
    print(f"碰撞状态：{snapshot.collision_occurred}")
    print(f"关节角（弧度）：{snapshot.joint_positions_rad}")
    print(f"工具位姿（米/弧度）：{snapshot.tool_pose_m_rad}")


if __name__ == "__main__":
    main()
