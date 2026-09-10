'Automatically collect task card 1 and task card 2 from VisionMaster.'

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from assembly.visionmaster_tcp import (  
    VisionMasterTaskCardCollector,
    save_task_cards,
)
from assembly.settings import load_visionmaster_collection_config  


def main() -> int:
    print("正在自动等待 VisionMaster 的任务卡 1 和任务卡 2 图像……")
    config_path = PROJECT_ROOT / "config" / "competition_config.toml"
    config = load_visionmaster_collection_config(config_path)
    print(f"已加载赛前 VisionMaster 配置：{config.tcp.host}:{config.tcp.port}")
    collector = VisionMasterTaskCardCollector(config)
    cards = collector.collect_task_cards()
    card1_path, card2_path = save_task_cards(
        cards, PROJECT_ROOT / "runtime" / "task_cards"
    )
    print(f"任务卡 1 已保存：{card1_path}")
    print(f"任务卡 2 已保存：{card2_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
