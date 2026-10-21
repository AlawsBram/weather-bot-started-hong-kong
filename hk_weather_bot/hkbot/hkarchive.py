"""Past HKO data for the no-peek backtest, from official archives.

1. HKO's OWN forecast as it was issued: the hourly "Local Weather Forecast" bulletin
   (rss.weather.gov.hk/rss/LocalWeatherForecast.xml), archived every hour by the Hong Kong
   government open-data portal (data.gov.hk historical archive). The 9-day forecast itself is
   not archived; this bulletin is. Wording seen 2026-03..10:
       "... with a maximum temperature of around 32 degrees ..."
       "The maximum temperature will be around 33 degrees ..."
       "Temperatures will range between 26 and 31 degrees ..."   (evening issue, for tomorrow)
   The morning issue is "Weather forecast for today", the 17:02 issue "...for tonight and tomorrow".
2. The live "max since midnight" at the HKO station, as it read at the decision time
   (the same CSV the live bot reads), archived about every 10 minutes.
3. The official daily maximum from HKO's Weather and Radiation Level Report (RYES), documented
   in the HKO Open Data API PDF v1.12: published 01:30 HKT the next day, back to 2019-09-10.
   It matched the Daily Extract (the market's resolution source) on every October 2026 day.

Every archived file is used ONLY if its archive timestamp is at or before the decision time.
Archive timestamps are Hong Kong time (the 08:02 file says "today", the 17:02 file "tomorrow").
"""
from __future__ import annotations

import csv
import io
import re
import time as _time
from datetime import date, datetime, timedelta

from . import config
from .sources import HKT

ARCHIVE = "https://api.data.gov.hk/v1/historical-archive"
LOCAL_FORECAST_URL = "https://rss.weather.gov.hk/rss/LocalWeatherForecast.xml"
SINCE_MIDNIGHT_URL = config.HKO_SINCE_MIDNIGHT_CSV
RYES_URL = config.HKO_OPENDATA_CLIMATE          # opendata.php?dataType=RYES&date=YYYYMMDD&station=HKO


# ----------------------------------------------------------------------------
# Parsers (unit-tested)
# ----------------------------------------------------------------------------

_MAX_PATTERNS = [
    re.compile(r"maximum temperatures? (?:will be |of )?(?:around|about|near) (\d{1,2}) degrees", re.I),
    re.compile(r"maximum temperatures? (?:will be )?(?:of )?(?:around |about )?(\d{1,2}) degrees", re.I),
    re.compile(r"temperatures? will range between \d{1,2} and (\d{1,2}) degrees", re.I),
    re.compile(r"range between \d{1,2} and (\d{1,2}) degrees", re.I),
]


def clean_text(raw: str) -> str:
    # The RSS wraps the bulletin in <![CDATA[ ... ]]>; unwrap it first, or the tag remover
    # below would swallow the whole bulletin as one "tag".
    raw = raw.replace("<![CDATA[", " ").replace("]]>", " ")
    raw = re.sub(r"&lt;[^&]*&gt;", " ", raw)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def parse_local_forecast(raw: str) -> tuple[str | None, float | None]:
    """-> (period, forecast max).  period = 'today' or 'tomorrow' (None if unclear).
    Only the forecast paragraph is read (it stops at 'Outlook:'), so the outlook for later
    days can never be mistaken for the forecast."""
    t = clean_text(raw)
    m = re.search(r"Weather forecast for ([^:]{1,60}):(.*?)(?:Outlook:|$)", t, re.I)
    if not m:
        return None, None
    head, body = m.group(1).lower(), m.group(2)
    period = "tomorrow" if "tomorrow" in head else ("today" if ("today" in head or "this afternoon" in head
                                                                or "this morning" in head) else None)
    for pat in _MAX_PATTERNS:
        x = pat.search(body)
        if x:
            return period, float(x.group(1))
    return period, None


def parse_since_midnight(text: str) -> tuple[datetime | None, float | None]:
    """The max-since-midnight CSV -> (its own timestamp, HKO station max)."""
    for row in csv.reader(io.StringIO(text.lstrip("﻿"))):
        if len(row) >= 3 and row[1].strip().lower() in ("hk observatory", "hong kong observatory"):
            try:
                ts = datetime.strptime(row[0].strip(), "%Y%m%d%H%M").replace(tzinfo=HKT)
                return ts, float(row[2])
            except ValueError:
                return None, None
    return None, None


def parse_ryes(payload) -> float | None:
    if not isinstance(payload, dict):
        return None
    v = payload.get("HKOReadingsMaxTemp") or payload.get("HKOMaxTemp")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def latest_at_or_before(timestamps: list[str], when: datetime) -> str | None:
    """Archive timestamps 'YYYYMMDD-HHMM' (HKT) -> the newest one not after `when`."""
    lim = when.astimezone(HKT).strftime("%Y%m%d-%H%M")
    ok = [t for t in timestamps if t <= lim]
    return max(ok) if ok else None


# ----------------------------------------------------------------------------
# Fetchers (through the history Cache, so reruns are instant and inspectable)
# ----------------------------------------------------------------------------

def _versions(cache, url: str, day: date) -> list[str]:
    blob = cache.get(f"{ARCHIVE}/list-file-versions",
                     {"url": url, "start": day.strftime("%Y%m%d"), "end": day.strftime("%Y%m%d")})
    data = (blob or {}).get("data") or {}
    return data.get("timestamps", []) if isinstance(data, dict) else []


def _file_text(cache, url: str, ts: str) -> str | None:
    blob = cache.get_text(f"{ARCHIVE}/get-file", {"url": url, "time": ts})
    return None if blob is None else blob.get("data")


def hko_forecast_at(cache, target: date, decision_dt: datetime) -> tuple[float | None, str | None]:
    """HKO's own forecast max for `target`, from the newest bulletin issued by `decision_dt`.
    Returns (value, archive timestamp used)."""
    want = "tomorrow" if decision_dt.date() < target else "today"
    day = decision_dt.date()
    stamps = _versions(cache, LOCAL_FORECAST_URL, day)
    # newest first, but only bulletins at or before the decision, and for the right day
    for ts in sorted((t for t in stamps if t <= decision_dt.strftime("%Y%m%d-%H%M")), reverse=True):
        txt = _file_text(cache, LOCAL_FORECAST_URL, ts)
        if not txt:
            continue
        period, v = parse_local_forecast(txt)
        if period == want and v is not None:
            return v, ts
        if period == want:            # right bulletin but no number in it: stop, don't use an older one
            return None, ts
    return None, None


def max_so_far_at(cache, day: date, decision_dt: datetime) -> tuple[float | None, str | None]:
    """HKO station max since midnight as it read at the decision time."""
    ts = latest_at_or_before(_versions(cache, SINCE_MIDNIGHT_URL, day), decision_dt)
    if not ts:
        return None, None
    txt = _file_text(cache, SINCE_MIDNIGHT_URL, ts)
    if not txt:
        return None, ts
    rec_ts, v = parse_since_midnight(txt)
    # the reading must be from that same day and not after the decision
    if rec_ts is None or rec_ts.date() != day or rec_ts > decision_dt:
        return None, ts
    return v, ts


def ryes_max(cache, day: date) -> float | None:
    blob = cache.get(RYES_URL, {"dataType": "RYES", "date": day.strftime("%Y%m%d"),
                                "lang": "en", "station": "HKO"})
    return parse_ryes((blob or {}).get("data"))


def polite_pause() -> None:
    _time.sleep(0.15)          # data.gov.hk is a public service: a few requests a second at most
