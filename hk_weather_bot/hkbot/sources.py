"""Data fetchers: Polymarket (Gamma API), Hong Kong Observatory open data, Open-Meteo models.

Every fetcher is defensive: providers change formats, so failures return None /
empty results and the bot carries on with the sources it does have.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config
from .buckets import Bucket, parse_label

log = logging.getLogger(__name__)
HKT = ZoneInfo(config.HK_TZ)
_session = requests.Session()
_session.headers["User-Agent"] = "hk-weather-paper-bot/0.1 (research; paper trading)"


def _get(url: str, params: dict | None = None, retries: int = 2, timeout: int = 20):
    last = None
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # network / HTTP errors
            last = e
            time.sleep(1.5 * (attempt + 1))
    log.warning("GET failed %s %s: %s", url, params, last)
    return None


# ----------------------------------------------------------------------------
# Polymarket
# ----------------------------------------------------------------------------

def event_slug(d: date) -> str:
    return f"highest-temperature-in-hong-kong-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"


@dataclass
class MarketBucket:
    bucket: Bucket
    yes_bid: float | None      # best price someone will pay for YES (you sell YES / buy NO at 1-bid)
    yes_ask: float | None      # best price to buy YES
    yes_price: float | None    # displayed probability (Polymarket outcomePrices)
    resolved_yes: bool | None  # True/False once settled, None while open
    volume: float = 0.0
    yes_token: str | None = None
    no_token: str | None = None
    fee_rate: float | None = None   # taker fee rate from the market's feeSchedule (None = unknown)

    @property
    def mid(self) -> float | None:
        if self.yes_bid is not None and self.yes_ask is not None and self.yes_ask >= self.yes_bid:
            return (self.yes_bid + self.yes_ask) / 2
        return self.yes_price


@dataclass
class MarketSnapshot:
    slug: str
    date: date
    buckets: list[MarketBucket]
    closed: bool
    fetched_at: datetime

    @property
    def url(self) -> str:
        return config.POLYMARKET_EVENT_PAGE.format(slug=self.slug)

    def winner(self) -> MarketBucket | None:
        won = [b for b in self.buckets if b.resolved_yes]
        return won[0] if len(won) == 1 else None

    def leader(self) -> MarketBucket | None:
        priced = [b for b in self.buckets if b.yes_price is not None]
        return max(priced, key=lambda b: b.yes_price) if priced else None


def _f(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _json_list(x) -> list:
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except json.JSONDecodeError:
            return []
    return []


def parse_gamma_event(event: dict, d: date) -> MarketSnapshot:
    buckets: list[MarketBucket] = []
    for m in event.get("markets", []):
        label = m.get("groupItemTitle") or m.get("question", "")
        try:
            b = parse_label(label)
        except ValueError:
            log.warning("Skipping unparseable bucket %r", label)
            continue
        prices = [_f(p) for p in _json_list(m.get("outcomePrices"))]
        yes_price = prices[0] if prices else None
        resolved = None
        if m.get("closed") and yes_price is not None and yes_price in (0.0, 1.0):
            resolved = yes_price == 1.0
        tokens = [str(t) for t in _json_list(m.get("clobTokenIds"))]
        fs = m.get("feeSchedule") if isinstance(m.get("feeSchedule"), dict) else {}
        fee_rate = _f(fs.get("rate")) if m.get("feesEnabled", True) else 0.0
        buckets.append(MarketBucket(
            bucket=b,
            yes_bid=_f(m.get("bestBid")),
            yes_ask=_f(m.get("bestAsk")),
            yes_price=yes_price,
            resolved_yes=resolved,
            volume=_f(m.get("volume")) or 0.0,
            yes_token=tokens[0] if len(tokens) > 0 else None,
            no_token=tokens[1] if len(tokens) > 1 else None,
            fee_rate=fee_rate,
        ))
    buckets.sort(key=lambda mb: mb.bucket.lo)
    return MarketSnapshot(
        slug=event.get("slug", event_slug(d)),
        date=d,
        buckets=buckets,
        closed=bool(event.get("closed")),
        fetched_at=datetime.now(HKT),
    )


def fetch_market_raw(d: date) -> dict | None:
    """The Gamma event exactly as Polymarket returns it (kept in each snapshot as a record)."""
    slug = event_slug(d)
    r = _get(config.GAMMA_EVENTS_URL, {"slug": slug})
    if r is None:
        return None
    data = r.json()
    events = data if isinstance(data, list) else [data]
    if not events or not events[0]:
        log.warning("No Polymarket event for %s", slug)
        return None
    return events[0]


def fetch_market(d: date) -> MarketSnapshot | None:
    ev = fetch_market_raw(d)
    return parse_gamma_event(ev, d) if ev else None


# ----------------------------------------------------------------------------
# Polymarket order books (every bid and ask with sizes, not just the top price)
# ----------------------------------------------------------------------------

def _book_levels(book: dict) -> dict:
    """CLOB book -> {"bids": [[price, size] best first], "asks": [[price, size] best first]}."""
    bids = sorted(([float(x["price"]), float(x["size"])] for x in book.get("bids", [])), reverse=True)
    asks = sorted([float(x["price"]), float(x["size"])] for x in book.get("asks", []))
    return {"bids": bids, "asks": asks, "timestamp": book.get("timestamp"),
            "tick_size": book.get("tick_size"), "min_order_size": book.get("min_order_size")}


def fetch_books(token_ids: list[str]) -> dict[str, dict]:
    """{token_id: book} for many tokens. One POST to /books, falling back to one GET each."""
    token_ids = [t for t in token_ids if t]
    out: dict[str, dict] = {}
    if not token_ids:
        return out
    try:
        r = _session.post(config.CLOB_BOOKS_URL, json=[{"token_id": t} for t in token_ids], timeout=20)
        r.raise_for_status()
        for b in r.json():
            if b.get("asset_id"):
                out[str(b["asset_id"])] = _book_levels(b)
    except (requests.RequestException, ValueError) as e:
        log.warning("POST /books failed (%s); fetching one by one", e)
    for t in token_ids:
        if t not in out:
            r = _get(config.CLOB_BOOK_URL, {"token_id": t}, retries=1)
            if r is not None:
                try:
                    out[t] = _book_levels(r.json())
                except ValueError:
                    pass
    return out


def fill_price(levels, usd: float) -> tuple[float | None, float]:
    """Average price paid to spend `usd` walking the book (asks, best first).

    Returns (average price, shares filled). If the book is too thin, the average covers
    what was available and shares filled is less than usd / best price."""
    spent = shares = 0.0
    for price, size in levels:
        if price <= 0:
            continue
        take = min(size, (usd - spent) / price)
        if take <= 0:
            break
        spent += take * price
        shares += take
        if spent >= usd - 1e-9:
            break
    return (spent / shares if shares else None), shares


def taker_fee_per_share(price: float, rate: float | None) -> float:
    """Polymarket taker fee per share: rate * p * (1 - p) (feeSchedule exponent 1)."""
    r = config.DEFAULT_TAKER_FEE_RATE if rate is None else rate
    return r * price * (1.0 - price)


# ----------------------------------------------------------------------------
# Hong Kong Observatory
# ----------------------------------------------------------------------------

def fetch_hko_9day() -> dict[date, float]:
    """Official HKO forecast max for each of the next 9 days (whole degrees)."""
    r = _get(config.HKO_OPENDATA_WEATHER, {"dataType": "fnd", "lang": "en"})
    out: dict[date, float] = {}
    if r is None:
        return out
    for day in r.json().get("weatherForecast", []):
        try:
            d = datetime.strptime(day["forecastDate"], "%Y%m%d").date()
            out[d] = float(day["forecastMaxtemp"]["value"])
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fetch_hko_max_since_midnight() -> float | None:
    """Today's running max at the HKO station, one decimal (None if unavailable)."""
    r = _get(config.HKO_SINCE_MIDNIGHT_CSV)
    if r is None:
        return None
    text = r.content.decode("utf-8-sig", errors="replace")
    for row in csv.reader(io.StringIO(text)):
        # The CSV labels the station "HK Observatory" (seen 2026-10-21), not "Hong Kong Observatory".
        if len(row) >= 3 and row[1].strip().lower() in ("hk observatory", "hong kong observatory"):
            return _f(row[2].strip())
    return None


def parse_clmmaxt(payload) -> dict[date, float]:
    """Parse HKO CLMMAXT (daily max) open-data JSON: rows like ["2026","10","15","29.7","C"]."""
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    out: dict[date, float] = {}
    for row in rows:
        try:
            y, m, d, v = int(row[0]), int(row[1]), int(row[2]), float(row[3])
            out[date(y, m, d)] = v
        except (ValueError, TypeError, IndexError):
            continue
    return out


def parse_daily_extract(payload, year: int) -> dict[date, float]:
    """HKO Daily Extract data file -> {date: Absolute Daily Max}.

    Rows look like ["19","1015.4","31.9","28.5",...]: day, pressure, ABSOLUTE MAX, mean, min ...
    Summary rows ("Mean/Total", "Normal") are skipped."""
    out: dict[date, float] = {}
    try:
        blocks = payload["stn"]["data"]
    except (KeyError, TypeError):
        return out
    for blk in blocks:
        month = int(blk.get("month", 0))
        for row in blk.get("dayData", []):
            try:
                out[date(year, month, int(row[0]))] = float(str(row[2]).strip())
            except (ValueError, TypeError, IndexError):
                continue
    return out


def fetch_daily_extract(year: int, month: int) -> dict[date, float]:
    """The figure the market resolves on, from the same data file the Daily Extract page shows."""
    r = _get(config.HKO_DAILY_EXTRACT.format(yyyymm=f"{year}{month:02d}"))
    if r is None:
        return {}
    try:
        return parse_daily_extract(json.loads(r.content.decode("utf-8-sig")), year)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def fetch_clmmaxt(year: int, month: int) -> dict[date, float]:
    """HKO open-data CLMMAXT. Same number, but filled in later (no October rows on 2026-10-21)."""
    r = _get(config.HKO_OPENDATA_CLIMATE, {
        "dataType": "CLMMAXT", "rformat": "json", "station": "HKO",
        "year": year, "month": month,
    })
    if r is None:
        return {}
    try:
        return parse_clmmaxt(r.json())
    except json.JSONDecodeError:
        return {}


def fetch_hko_daily_max(year: int, month: int) -> dict[date, float]:
    """Absolute daily max at HKO HQ — the figure the market resolves on.
    Daily Extract first (updated every day); CLMMAXT only for days it lacks."""
    out = fetch_clmmaxt(year, month)
    out.update(fetch_daily_extract(year, month))
    return out


def parse_web_fnd(payload) -> dict[date, float]:
    """The 9-day page's own data file (DYN_DAT_MINDS_FND.json) -> {date: forecast max}."""
    d = payload.get("DYN_DAT_MINDS_FND", {}) if isinstance(payload, dict) else {}
    out: dict[date, float] = {}
    for i in range(1, 10):
        try:
            day = datetime.strptime(d[f"Day{i}ForecastDate"]["Value_Eng"], "%Y%m%d").date()
            out[day] = float(d[f"Day{i}MaxTemp"]["Value_Eng"])
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fetch_hko_web_fnd() -> dict[date, float]:
    r = _get(config.HKO_WEB_FND_JSON)
    if r is None:
        return {}
    try:
        return parse_web_fnd(json.loads(r.content.decode("utf-8-sig")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def fetch_hko_current_temp() -> float | None:
    """Latest hourly temperature at the HKO station (open-data rhrread)."""
    r = _get(config.HKO_CURRENT_WEATHER, {"dataType": "rhrread", "lang": "en"})
    if r is None:
        return None
    try:
        for x in r.json().get("temperature", {}).get("data", []):
            if x.get("place", "").lower() == "hong kong observatory":
                return _f(x.get("value"))
    except ValueError:
        pass
    return None


def fetch_ryes_max(d: date) -> float | None:
    """HKO Weather and Radiation Level Report (documented, API PDF v1.12): official max for day d,
    published 01:30 HKT the next day. Matched the Daily Extract on every October 2026 day."""
    r = _get(config.HKO_OPENDATA_CLIMATE, {"dataType": "RYES", "date": d.strftime("%Y%m%d"),
                                           "lang": "en", "station": "HKO"}, retries=1)
    if r is None:
        return None
    try:
        return _f(r.json().get("HKOReadingsMaxTemp"))
    except ValueError:
        return None


def fetch_hko_local_forecast() -> dict:
    """HKO Local Weather Forecast (flw): its own forecast max for today or tomorrow, plus the
    tropical-cyclone note. Same bulletin the history backtest reads from the archive."""
    from .hkarchive import parse_local_forecast
    r = _get(config.HKO_OPENDATA_WEATHER, {"dataType": "flw", "lang": "en"})
    if r is None:
        return {}
    try:
        j = r.json()
    except ValueError:
        return {}
    period, vmax = parse_local_forecast(
        f"{j.get('forecastPeriod', '')}: {j.get('forecastDesc', '')} Outlook: {j.get('outlook', '')}")
    upd = j.get("updateTime")
    target = None
    if period and upd:
        issued = datetime.fromisoformat(upd).astimezone(HKT).date()
        target = issued + timedelta(days=1) if period == "tomorrow" else issued
    return {"period": period, "max": vmax, "target": target, "update_time": upd,
            "tc_info": j.get("tcInfo") or "", "text": j.get("forecastDesc", "")}


def fetch_hko_warnings() -> dict:
    """HKO warning summary (warnsum): {code: name} of warnings in force (e.g. WHOT, WRAIN, WTCSGNL)."""
    r = _get(config.HKO_OPENDATA_WEATHER, {"dataType": "warnsum", "lang": "en"})
    if r is None:
        return {"_error": "unreadable"}
    try:
        j = r.json() or {}
    except ValueError:
        return {"_error": "unreadable"}
    return {k: f"{v.get('name', '')} ({v.get('code', '')}, {v.get('actionCode', '')})"
            for k, v in j.items() if isinstance(v, dict) and v.get("actionCode") != "CANCEL"}


def fetch_hko_daily_max_range(start: date, end: date) -> dict[date, float]:
    out: dict[date, float] = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.update(fetch_hko_daily_max(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {d: v for d, v in out.items() if start <= d <= end}


# ----------------------------------------------------------------------------
# Open-Meteo (ECMWF / GFS / ICON at the HKO station point, hourly -> daily max)
# ----------------------------------------------------------------------------

def _daily_max_from_hourly(payload: dict, key: str) -> dict[date, float]:
    hourly = payload.get("hourly", {})
    times, temps = hourly.get("time", []), hourly.get(key, [])
    out: dict[date, float] = {}
    for t, v in zip(times, temps):
        if v is None:
            continue
        d = datetime.fromisoformat(t).date()
        out[d] = max(out.get(d, -99.0), float(v))
    return out


def fetch_model_forecasts(start: date, end: date, historical: bool = False) -> dict[str, dict[date, float]]:
    """{source_name: {date: forecast daily max}} for each configured model."""
    url = config.OPEN_METEO_HISTORICAL_FORECAST if historical else config.OPEN_METEO_FORECAST
    out: dict[str, dict[date, float]] = {}
    for model_id, name in config.MODEL_SOURCES.items():
        r = _get(url, {
            "latitude": config.HKO_LAT, "longitude": config.HKO_LON,
            "hourly": "temperature_2m", "models": model_id,
            "timezone": config.HK_TZ,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        })
        if r is None:
            continue
        payload = r.json()
        key = "temperature_2m" if "temperature_2m" in payload.get("hourly", {}) else f"temperature_2m_{model_id}"
        series = _daily_max_from_hourly(payload, key)
        if series:
            out[name] = series
    return out


def now_hkt() -> datetime:
    return datetime.now(HKT)


def today_hkt() -> date:
    return now_hkt().date()


def tomorrow_hkt() -> date:
    return today_hkt() + timedelta(days=1)
