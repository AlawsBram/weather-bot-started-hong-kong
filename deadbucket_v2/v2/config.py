"""Settings. Everything is overridable from the environment (deploy/.env on the VM, never committed)."""
import os
from pathlib import Path

NAME = "dead-bucket v2"
DESCRIPTION = ("~50 cities (all Polymarket daily-temperature markets except Hong Kong). Trigger = the resolution "
               "feed itself (aviationweather.gov METAR, one request for all stations every 5 s). A bucket the day's "
               "high/low has already passed is dead -> buy NO (paper). Rules re-read every 6 h, station changes alerted.")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DB2_DATA_DIR", ROOT / "data"))
STATE_DIR = DATA_DIR / "state"
STATIONS_FILE = DATA_DIR / "stations.json"
DECISIONS_FILE = DATA_DIR / "decisions.jsonl"
FEED_LAG_FILE = DATA_DIR / "feed_lag.csv"
LEDGER_FILE = DATA_DIR / "ledger.csv"

MODE = os.environ.get("DB2_MODE", "dry")                       # dry | probe | live
POLL_SECONDS = float(os.environ.get("DB2_POLL_SECONDS", "5"))  # one METAR request for ALL stations per poll
RULES_REFRESH_HOURS = float(os.environ.get("DB2_RULES_REFRESH_HOURS", "6"))
STAKE_USD = float(os.environ.get("DB2_STAKE_USD", "10"))       # paper (dry) stake per dead bucket
MAX_NO_PRICE = float(os.environ.get("DB2_MAX_NO_PRICE", "0.97"))
MIN_NO_PRICE = float(os.environ.get("DB2_MIN_NO_PRICE", "0.50"))   # cheaper than this = something is off, log only
MIN_MARGIN_TENTHS = int(os.environ.get("DB2_MIN_MARGIN_TENTHS", "0"))  # extra safety past the kill line
LIVE_MAX_USD_PER_ORDER = float(os.environ.get("DB2_LIVE_MAX_USD", "5"))
LIVE_MAX_ORDERS_PER_DAY = int(os.environ.get("DB2_LIVE_MAX_ORDERS_PER_DAY", "20"))
PROBE_PRICE = float(os.environ.get("DB2_PROBE_PRICE", "0.50"))     # far below market: rests, then is cancelled
PROBE_SHARES = float(os.environ.get("DB2_PROBE_SHARES", "5"))      # Polymarket minimum order size
KILL_FILE = ROOT / "KILL"
