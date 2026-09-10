'Capture one task-card image from the known VisionMaster TCP service.'

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from assembly.visionmaster_tcp import (  
    VisionMasterTcpConfig,
    VisionMasterTcpImageClient,
    save_task_image,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 VisionMaster TCP 服务接收一张任务卡图像。"
    )
    parser.add_argument("--card", type=int, choices=(1, 2), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7930)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "task_cards",
    )
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--idle-timeout", type=float, default=1.0)
    args = parser.parse_args()

    client = VisionMasterTcpImageClient(
        VisionMasterTcpConfig(
            host=args.host,
            port=args.port,
            connect_timeout_s=args.connect_timeout,
            idle_timeout_s=args.idle_timeout,
        )
    )
    print(f"正在连接 VisionMaster：{args.host}:{args.port}")
    image = client.capture_next_image()
    path = save_task_image(image, args.output_dir, args.card)
    print(f"已保存任务卡 {args.card}：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
