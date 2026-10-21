"""HK weather bot, version 2 (research backtest). See README.md.

v2 reuses v1's data downloaders from the sibling folder `hk_weather_bot/` (read-only)."""
import sys
from pathlib import Path

_V1 = Path(__file__).resolve().parents[2] / "hk_weather_bot"
if str(_V1) not in sys.path:
    sys.path.insert(0, str(_V1))
