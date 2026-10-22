"""HKO sources (all public) and the polling schedule."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone

from . import config

HKT = timezone(timedelta(hours=8))
SOURCES = {
    "text_hko": "https://www.hko.gov.hk/textonly/v2/forecast/text_readings_e.htm",
    "text_wx": "https://www.weather.gov.hk/wxinfo/ts/text_readings_e.htm",
    "csv_maxmin": "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_since_midnight_maxmin.csv",
    "csv_1min": "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv",
    "rhrread": "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en",
}
TRIGGER = "text_hko"


def cutoff_value(reading: float) -> int:
    """HK rule: 31.9 counts as 31 (one decimal, decimals cut off)."""
    return math.floor(round(reading, 1) + 1e-9)


def parse(src: str, text: str):
    """-> (reading_dt HKT, temp, max, min) or None."""
    if src in ("csv_1min", "csv_maxmin"):
        for line in text.splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 3 and p[1] == "HK Observatory":
                dt = datetime.strptime(p[0], "%Y%m%d%H%M").replace(tzinfo=HKT)
                if src == "csv_1min":
                    return dt, float(p[2]), None, None
                return dt, None, float(p[2]), float(p[3])
        return None
    if src.startswith("text_"):
        flat = re.sub(r"<[^>]+>", " ", text)
        m = re.search(r"recorded at (\d{1,2}):(\d{2}) Hong Kong Time (\d{1,2}) (\w+) (\d{4})", flat)
        r = re.search(r"HK Observatory\s+(-?\d+\.\d)\s+\d+\s+(-?\d+\.\d)\s*/\s*(-?\d+\.\d)", flat)
        if not (m and r):
            return None
        dt = datetime.strptime(f"{m.group(3)} {m.group(4)} {m.group(5)} {m.group(1)}:{m.group(2)}",
                               "%d %B %Y %H:%M").replace(tzinfo=HKT)
        return dt, float(r.group(1)), float(r.group(2)), float(r.group(3))
    if src == "rhrread":
        d = json.loads(text)
        dt = datetime.fromisoformat(d["temperature"]["recordTime"])
        v = next((x["value"] for x in d["temperature"]["data"] if x["place"] == "Hong Kong Observatory"), None)
        return (dt.astimezone(HKT), float(v), None, None) if v is not None else None
    return None


def is_day(now: datetime | None = None) -> bool:
    hk = (now or datetime.now(timezone.utc)).astimezone(HKT)
    return config.DAY_START <= hk.hour + hk.minute / 60 <= config.DAY_END


def in_publish_window(now: datetime | None = None) -> bool:
    hk = (now or datetime.now(timezone.utc)).astimezone(HKT)
    m = (hk.minute % 10) + hk.second / 60
    return config.WINDOW_FROM <= m <= config.WINDOW_TO


def interval(src: str, now: datetime | None = None) -> float:
    if not is_day(now):
        return config.NIGHT_INTERVAL
    fast = config.FAST_INTERVAL[src]
    return fast if in_publish_window(now) else max(fast, config.OUTSIDE_INTERVAL)
