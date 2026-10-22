"""Discover every active daily-temperature market on Polymarket and read ITS OWN rules.

The station, the unit and the day window are extracted from each event's rules text (what the
"Rules" panel on the page shows = the Gamma API `description` + `resolutionSource`), never from a
hand-written table. A table of (city, kind) -> station is kept in data/stations.json only so that
CHANGES can be noticed and alerted; trading always uses the event's own parsed rules.

Rule styles seen on 2026-10-22:
  metar  : aviationweather.gov METAR feed, tenths of a degree, round half up (new, from Oct 23)
  noaa   : weather.gov timeseries page, whole degrees (Oct 20-22 events)   -> not traded
  wu     : Wunderground daily history page (old)                          -> not traded
  hko    : Hong Kong Observatory (HK has its own bot)                       -> not traded
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .buckets import Bucket, parse_label

GAMMA = "https://gamma-api.polymarket.com"
TAG_DAILY_TEMPERATURE = "103040"
SLUG_RE = re.compile(r"^(highest|lowest)-temperature-in-(.+)-on-([a-z]+-\d{1,2}-\d{4})$")
METAR_URL_RE = re.compile(r"aviationweather\.gov/api/data/metar\?ids=([A-Z0-9]{4})&format=raw&date="
                          r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z&hours=(\d+)")
ICAO_IN_TEXT_RE = re.compile(r"\(([A-Z][A-Z0-9]{3})\)")
NOAA_RE = re.compile(r"weather\.gov/wrh/timeseries\?site=([a-z0-9]{3,4})", re.I)
WU_RE = re.compile(r"wunderground\.com/history/daily/[^\s]*/([A-Z0-9]{3,4})\b")
# the rules URL's `date` is local midnight (end of the observation day) + 3 h
METAR_URL_AFTER_MIDNIGHT = timedelta(hours=3)


@dataclass
class Market:
    label: str
    bucket: Bucket
    market_id: str
    condition_id: str
    yes_token: str
    no_token: str
    closed: bool
    outcome_prices: list


@dataclass
class Event:
    slug: str
    kind: str                     # highest | lowest
    city: str
    day: date                     # observation date (station local)
    style: str                    # metar | noaa | wu | hko | unknown
    icao: str | None
    unit: str | None              # C | F
    window_start: datetime | None  # UTC, inclusive
    window_end: datetime | None    # UTC, exclusive
    markets: list[Market] = field(default_factory=list)
    rules_text: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.style == "metar" and bool(self.icao) and self.window_start is not None and not self.problems

    @property
    def key(self) -> str:
        return f"{self.city}|{self.kind}"


def discover(session: requests.Session | None = None) -> list[dict]:
    """All active, not-closed daily-temperature events (raw Gamma objects)."""
    s = session or requests.Session()
    out, off = [], 0
    while True:
        r = s.get(f"{GAMMA}/events", params={"tag_id": TAG_DAILY_TEMPERATURE, "active": "true",
                                              "closed": "false", "limit": 100, "offset": off}, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += d
        if len(d) < 100:
            return out
        off += 100


def parse_event(e: dict) -> Event | None:
    m = SLUG_RE.match(e.get("slug") or "")
    if not m:
        return None
    kind, city, dstr = m.groups()
    day = datetime.strptime(dstr, "%B-%d-%Y").date()
    text = (e.get("resolutionSource") or "") + "\n" + (e.get("description") or "")
    ev = Event(slug=e["slug"], kind=kind, city=city, day=day, style="unknown", icao=None, unit=None,
               window_start=None, window_end=None, rules_text=text)
    um = METAR_URL_RE.search(text)
    if um:
        ev.style = "metar"
        ev.icao = um.group(1)
        end = datetime.strptime(um.group(2), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) - METAR_URL_AFTER_MIDNIGHT
        ev.window_end, ev.window_start = end, end - timedelta(hours=24)
        named = set(ICAO_IN_TEXT_RE.findall(e.get("description") or ""))
        if named and ev.icao not in named:
            ev.problems.append(f"station in URL {ev.icao} differs from station named in text {sorted(named)}")
    elif NOAA_RE.search(text):
        ev.style, ev.icao = "noaa", NOAA_RE.search(text).group(1).upper()
    elif "wunderground" in text.lower():
        ev.style = "wu"
        wm = WU_RE.search(text)
        ev.icao = wm.group(1) if wm else None
    elif "weather.gov.hk" in text or "Hong Kong Observatory" in text:
        ev.style, ev.icao = "hko", "HKO"
    for mk in e.get("markets", []):
        label = mk.get("groupItemTitle") or ""
        try:
            b = parse_label(label)
        except ValueError:
            ev.problems.append(f"cannot parse bucket label {label!r}")
            continue
        toks = mk.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else (toks or [])
        if len(toks) != 2:
            ev.problems.append(f"no token ids for {label!r}")
            continue
        op = mk.get("outcomePrices")
        op = json.loads(op) if isinstance(op, str) else (op or [])
        ev.markets.append(Market(label, b, str(mk.get("id")), mk.get("conditionId") or "", str(toks[0]), str(toks[1]),
                                 bool(mk.get("closed")), [float(x) for x in op]))
        ev.unit = ev.unit or b.unit
    ev.markets.sort(key=lambda x: (x.bucket.lo if x.bucket.lo is not None else -10 ** 6))
    if not ev.markets:
        ev.problems.append("no buckets")
    return ev


def load_events(session=None) -> list[Event]:
    evs = [parse_event(e) for e in discover(session)]
    return [e for e in evs if e]


# ----------------------------------------------------------------------------
# Station table + change detection
# ----------------------------------------------------------------------------

def update_station_table(events: list[Event], path: Path) -> list[str]:
    """Merge today's parsed rules into data/stations.json; return human-readable CHANGES."""
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    new = dict(old)
    changes = []
    today = date.today().isoformat()
    for ev in sorted(events, key=lambda x: (x.key, x.day)):
        cur = new.get(ev.key)
        rec = {"icao": ev.icao, "style": ev.style, "unit": ev.unit, "supported": ev.supported,
               "last_seen": today, "last_event": ev.slug,
               "first_seen": cur["first_seen"] if cur else today}
        if cur and (cur.get("icao") != ev.icao or cur.get("style") != ev.style):
            # only report a change when the NEWER event differs (older events may still carry old rules)
            if ev.slug >= cur.get("last_event", ""):
                changes.append(f"{ev.key}: {cur.get('style')}/{cur.get('icao')} -> {ev.style}/{ev.icao} ({ev.slug})")
            else:
                continue
        new[ev.key] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(new, indent=1, sort_keys=True), encoding="utf-8")
    return changes


# ----------------------------------------------------------------------------
# Optional: read the Rules panel on the actual web page (headless Chrome) and compare
# ----------------------------------------------------------------------------

def inspect_page(ev: Event, timeout_ms: int = 45000) -> dict:
    """Open polymarket.com/event/<slug>, read the page text, find the station the PAGE shows, and
    compare it with the station parsed from the API rules. Needs playwright + chromium."""
    from playwright.sync_api import sync_playwright
    url = f"https://polymarket.com/event/{ev.slug}"
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        page = br.new_page(viewport={"width": 1280, "height": 2000})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            for txt in ("Show more", "Rules"):
                try:
                    page.get_by_text(txt, exact=False).first.click(timeout=2000)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
            body = page.inner_text("body")
        finally:
            br.close()
    urls = METAR_URL_RE.findall(body)
    named = ICAO_IN_TEXT_RE.findall(body)
    page_icao = urls[0][0] if urls else (named[0] if named else None)
    return {"slug": ev.slug, "url": url, "api_icao": ev.icao, "page_icao": page_icao,
            "match": page_icao == ev.icao, "page_has_rules": "resolve" in body.lower(),
            "snippet": body[body.lower().find("resolve"):][:300].replace("\n", " ") if "resolve" in body.lower() else ""}
