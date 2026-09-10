'Open the one-click competition desktop entry.'

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from assembly.competition_gui import launch_gui  
from competition_variables import (
    ARCS_JSON_RPC_PORT,
    ARCS_ROBOT_IP,
    LOCAL_SETTINGS_PATH,
    ROBOT_PARAMETERS,
    TASK_CARD_IMAGE_DIRECTORY,
    VISIONMASTER_IP,
    VISIONMASTER_PORT,
)


if __name__ == "__main__":
    launch_gui(
        PROJECT_ROOT,
        connection_defaults={
            "visionmaster_host": VISIONMASTER_IP,
            "visionmaster_port": VISIONMASTER_PORT,
            "task_card_image_directory": TASK_CARD_IMAGE_DIRECTORY,
            "arcs_host": ARCS_ROBOT_IP,
            "arcs_port": ARCS_JSON_RPC_PORT,
        },
        robot_defaults=ROBOT_PARAMETERS,
        memory_path=LOCAL_SETTINGS_PATH,
    )
