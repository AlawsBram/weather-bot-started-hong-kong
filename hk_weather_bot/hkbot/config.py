"""All tunable settings live here.

Strategy *versions* are kept side by side so each new idea can be
forward-tested against older ones instead of replacing them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# --- Location of the resolution station (HKO headquarters, Tsim Sha Tsui) ---
HKO_LAT = 22.3022
HKO_LON = 114.1742
HK_TZ = "Asia/Hong_Kong"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# HKBOT_DB lets a test run use a throwaway ledger instead of the real one.
DB_PATH = Path(os.environ["HKBOT_DB"]) if os.environ.get("HKBOT_DB") else DATA_DIR / "ledger.sqlite"
CALIBRATION_PATH = DATA_DIR / "calibration.json"

# --- Endpoints (change here if a provider moves something) ---
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
POLYMARKET_EVENT_PAGE = "https://polymarket.com/event/{slug}"
HKO_OPENDATA_WEATHER = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
HKO_OPENDATA_CLIMATE = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
HKO_SINCE_MIDNIGHT_CSV = (
    "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/"
    "latest_since_midnight_maxmin.csv"
)
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# Forecasts exactly as issued N days earlier (no peeking) — used by the history backtest.
OPEN_METEO_PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
CLOB_PRICES_HISTORY = "https://clob.polymarket.com/prices-history"
HISTORY_DIR = DATA_DIR / "history"

# Found with the browser's Network tab (2026-10-21). These are the data files the HKO web pages
# themselves load, so they show exactly what a person sees on the page.
#  - Daily Extract table = the market's resolution source ("Absolute Daily Max"). Updated daily,
#    unlike CLMMAXT, which had no October rows on 2026-10-21.
HKO_DAILY_EXTRACT = "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{yyyymm}.xml"
HKO_WEB_FND_JSON = "https://www.hko.gov.hk/json/DYN_DAT_MINDS_FND.json"
HKO_CURRENT_WEATHER = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"   # dataType=rhrread
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
CLOB_BOOKS_URL = "https://clob.polymarket.com/books"

# Pages the snapshot reads and screenshots with headless Chrome (proof + cross-check).
PAGE_HKO_FND = "https://www.hko.gov.hk/en/wxinfo/currwx/fnd.htm"
PAGE_HKO_EXTRACT = "https://www.hko.gov.hk/en/cis/dailyExtract.htm?y={y}&m={m}"

# --- Snapshot settings ---
SNAPSHOT_DIR = DATA_DIR / "snapshots"
BACKUP_DIR = DATA_DIR / "backups"
STATE_DIR = DATA_DIR / "state"
SCREENSHOT_QUALITY = 60            # JPEG quality; a full page is ~150-400 KB
HKO_PAGE_EVERY_HOURS = 3           # HKO asks for light use of its website: pages at most every 3 h
HKO_PAGE_EXTRA_HKT = ("11:40", "16:40")   # ...plus right after its ~11:30 / ~16:30 bulletins
POLYMARKET_PAGE_EVERY_ROUND = True
PAGE_TIMEOUT_S = 60

# Cross-check tolerances (feed number vs number shown on the page)
XCHECK_PRICE_POINTS = 0.03         # Polymarket "Buy Yes" cents vs the API ask (3 cents)
XCHECK_TEMP_C = 0.05               # HKO numbers must match exactly (one decimal)

# Weather markets charge a TAKER fee (gamma feeSchedule on 2026-10-21: rate 0.05, takerOnly).
# Fee per share = rate * p * (1 - p). Read from each market when present; this is the fallback.
DEFAULT_TAKER_FEE_RATE = 0.05

# --- Alerts (Telegram) ---
# Token + chat are read from this env file (same bot as the rest of the fleet), thread from HK_TG_THREAD_ID.
TELEGRAM_ENV_FILES = (Path.home() / "hk_weather_bot.env", Path.home() / "s1_harness" / "telegram.env")
ALERT_DEDUP_HOURS = 6              # the same problem is reported at most once per 6 h
ALERT_MAX_PER_HOUR = 6             # global cap for the whole bot
SNAPSHOT_STALE_MIN = 75            # health check: no snapshot for this long = alert
DISK_MIN_FREE_GB = 10

# Open-Meteo model id -> our source name
MODEL_SOURCES = {
    "ecmwf_ifs025": "ecmwf",
    "gfs_seamless": "gfs",
    "icon_seamless": "icon",
}


@dataclass(frozen=True)
class SourcePrior:
    """Starting beliefs about a forecast source, before calibration data exists.

    bias  = average (forecast - actual). Positive means the source runs warm.
    sigma = typical size of its error in deg C (standard deviation).
    weight = how much we trust it relative to others (multiplies 1/sigma^2).
    """
    bias: float = 0.0
    sigma: float = 1.2
    weight: float = 1.0
    n: int = 0          # how many settled days the bias/sigma were measured on (0 = hand-written guess)


# Priors come from what we saw in the manual walkthrough (Oct 17-18, 2026):
# HKO official was right or +1 warm; ECMWF/meteoblue ran ~1 C cool; GFS ~3 C cool.
DEFAULT_PRIORS = {
    "hko_official": SourcePrior(bias=0.3, sigma=0.9, weight=1.5),
    "ecmwf": SourcePrior(bias=-0.8, sigma=1.2, weight=1.0),
    "icon": SourcePrior(bias=-0.8, sigma=1.4, weight=0.7),
    "gfs": SourcePrior(bias=-2.0, sigma=2.0, weight=0.3),
}


@dataclass(frozen=True)
class Strategy:
    version: str
    description: str
    # --- probability model ---
    use_cutoff_rule: bool = True          # bucket k = readings k.0 .. k.9
    use_bias_correction: bool = True
    sources: tuple = ("hko_official", "ecmwf", "icon", "gfs")
    sigma_floor: float = 0.7              # never be more confident than this
    sigma_scale: float = 1.0              # tuned by `calibrate`
    plausible_floor: float = 0.08         # min prob for a bucket any source points to
    tail_floor: float = 0.003             # min prob for every bucket
    market_blend: float = 0.5             # 1.0 = ignore market, 0.0 = copy market
    use_intraday_obs: bool = True         # use live "max since midnight"
    # --- trading rules ---
    min_edge: float = 0.06                # 6 points after paying the spread
    min_price: float = 0.03               # skip lottery tickets
    max_price: float = 0.97               # skip pennies-in-front-of-steamroller
    stake_usd: float = 10.0               # paper stake per trade
    max_trades_per_day: int = 3
    # --- weights ---
    auto_weights: bool = False            # True: trust each source by its MEASURED error size only


STRATEGIES = {
    # Reproduces the manual v1 from Oct 18: follow HKO, treat forecast as the bucket,
    # no cut-off rule, no market respect. Kept as a baseline to beat.
    "v1_naive": Strategy(
        version="v1_naive",
        description="HKO-only, rounds instead of cutting off, ignores market",
        use_cutoff_rule=False,
        use_bias_correction=False,
        sources=("hko_official",),
        plausible_floor=0.0,
        market_blend=1.0,
        use_intraday_obs=False,
    ),
    # Everything learned in the audit.
    "v2_audited": Strategy(
        version="v2_audited",
        description="Multi-source, bias-corrected, cut-off rule, 50% market blend",
    ),
    # v2 with ONE change: the hand-written trust multipliers (HKO 1.5, GFS 0.3 ...) fade out as
    # measured days come in, so each source is weighted only by its measured error size.
    "v3_auto_weights": Strategy(
        version="v3_auto_weights",
        description="v2 + weights from measured error size (hand-written trust fades out)",
        auto_weights=True,
    ),
}

DEFAULT_STRATEGY = "v2_audited"


def get_strategy(name: str, **overrides) -> Strategy:
    if name not in STRATEGIES:
        raise SystemExit(f"Unknown strategy '{name}'. Choose from: {', '.join(STRATEGIES)}")
    s = STRATEGIES[name]
    return replace(s, **overrides) if overrides else s
