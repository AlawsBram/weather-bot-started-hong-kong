"""The resolution feed itself: aviationweather.gov METAR API, many stations in ONE request.

Qualifying value per the rules: routine observations only (a line starting with SPECI does not
count); the T-group remark (T + 8 digits: sign, tenths C, dew point ignored) if present, else the
body temperature group (18/12 -> 18 C, M11/M17 -> -11 C) in whole degrees; an observation with
neither is disregarded. A report counts for the day whose local-time window contains its time.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

API = "https://aviationweather.gov/api/data/metar"
T_GROUP_RE = re.compile(r"(?:^|\s)T([01])(\d{3})[01]\d{3}(?=\s|$)")
BODY_TEMP_RE = re.compile(r"(?:^|\s)(M?)(\d{2})/(?:M?\d{2}|//)?(?=\s|$)")


@dataclass(frozen=True)
class Report:
    icao: str
    obs_time: datetime        # UTC
    raw: str
    routine: bool
    tenths_c: int | None      # qualifying value, None = disregarded
    seen: float               # unix time we first saw it


def qualifying_tenths(raw: str) -> tuple[bool, int | None]:
    """-> (routine?, tenths of a degree C or None)."""
    s = raw.strip()
    if s.upper().startswith("SPECI"):
        return False, None
    m = T_GROUP_RE.search(s)
    if m:
        v = int(m.group(2))
        return True, -v if m.group(1) == "1" else v
    body = s.split(" RMK ", 1)[0]
    b = BODY_TEMP_RE.search(body)
    if b:
        v = int(b.group(2)) * 10
        return True, -v if b.group(1) == "M" else v
    return True, None


def fetch(session: requests.Session, icaos: list[str], hours: int = 2, timeout: int = 15) -> list[Report]:
    if not icaos:
        return []
    r = session.get(API, params={"ids": ",".join(sorted(set(icaos))), "format": "json", "hours": hours}, timeout=timeout)
    r.raise_for_status()
    seen = time.time()
    out = []
    for x in r.json():
        raw = x.get("rawOb") or ""
        ts = x.get("obsTime")
        if not raw or ts is None:
            continue
        routine, tenths = qualifying_tenths(raw)
        if (x.get("metarType") or "").upper() == "SPECI":
            routine = False
        out.append(Report(x.get("icaoId") or "", datetime.fromtimestamp(int(ts), timezone.utc), raw, routine, tenths, seen))
    return out
