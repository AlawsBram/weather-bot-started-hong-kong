"""v3 settings; every value can be overridden from deploy/.env (never committed)."""
import os
from pathlib import Path

NAME = "HK dead-bucket v3.2 (several connections per HKO server IP, first wins)"
DESCRIPTION = ("Hong Kong only. v3.2 change: the HKO text 'latest readings' page is polled over SEVERAL independent "
               "keep-alive connections to each HKO web server IP (203.129.88.115 and 103.30.70.62, V3_CONNS_PER_IP each, "
               "default 3) plus V3_CONNS_NAMED (default 2) to www.hko.gov.hk, and the first connection to show a new stamp "
               "fires the kill logic. Reason: on 2026-10-27 which copy of the page a connection got was random (same stamp "
               "seen up to 58 s apart on two connections to the same host; v3.1's 3 sources each won about a third), so "
               "more connections = more draws. Polled every 2 s only in the 5.5-9.75 min publish window after each 10-min stamp. A bucket the max-since-midnight has already passed is dead -> buy NO (paper). "
               "Every poll, every source and the dead bucket's order book are recorded.")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("V3_DATA_DIR", ROOT / "data"))
STATE_DIR = DATA_DIR / "state"
DECISIONS_FILE = DATA_DIR / "decisions.jsonl"
LEDGER_FILE = DATA_DIR / "ledger.csv"
KILL_FILE = ROOT / "KILL"

MODE = os.environ.get("V3_MODE", "dry")                        # dry | probe | live
CONNS_PER_IP = int(os.environ.get("V3_CONNS_PER_IP", "3"))     # v3.2: keep-alive connections per HKO server IP
CONNS_NAMED = int(os.environ.get("V3_CONNS_NAMED", "2"))       # v3.2: keep-alive connections to www.hko.gov.hk by name
STAKE_USD = float(os.environ.get("V3_STAKE_USD", "10"))
MAX_NO_PRICE = float(os.environ.get("V3_MAX_NO_PRICE", "0.97"))
MIN_NO_PRICE = float(os.environ.get("V3_MIN_NO_PRICE", "0.50"))
LIVE_MAX_USD_PER_ORDER = float(os.environ.get("V3_LIVE_MAX_USD", "5"))
LIVE_MAX_ORDERS_PER_DAY = int(os.environ.get("V3_LIVE_MAX_ORDERS_PER_DAY", "10"))
PROBE_PRICE = float(os.environ.get("V3_PROBE_PRICE", "0.50"))
PROBE_SHARES = float(os.environ.get("V3_PROBE_SHARES", "5"))

# polling (HKT). HKO stamps every 10 min and publishes 7:15-8:45 later (measured 2026-10-22).
DAY_START, DAY_END = 6.5, 20.5
WINDOW_FROM, WINDOW_TO = 5.5, 9.75          # minutes after each 10-min stamp: fast polling
FAST_INTERVAL = {"text_ip203": 2, "text_ip103": 2, "text_hko": 2, "text_nocache": 10, "text_wx": 10, "csv_maxmin": 10, "csv_1min": 10, "rhrread": 30}
OUTSIDE_INTERVAL, NIGHT_INTERVAL = 20, 60
STALL_MIN = 25
