'Open the one-click competition desktop entry.'

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from assembly.competition_gui import launch_gui  


if __name__ == "__main__":
    launch_gui(PROJECT_ROOT)
