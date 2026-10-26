"""Today's Hong Kong market: buckets, tokens, order books, and what a taker NO buy really costs."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

HKBOT = Path(os.environ.get("HKBOT_DIR", Path.home() / "hkbot"))
if str(HKBOT) not in sys.path:
    sys.path.insert(0, str(HKBOT))
from hkbot import config as hkc  # noqa: E402
from hkbot import sources as hks  # noqa: E402
from hkbot.buckets import Bucket, parse_label  # noqa: E402

CLOB_BOOK = "https://clob.polymarket.com/book"
WEATHER_TAKER_FEE = 0.05


@dataclass
class Market:
    bucket: Bucket          # [lo, hi) in whole degrees, hi exclusive
    label: str
    market_id: str
    condition_id: str
    yes_token: str
    no_token: str


def load_day(session: requests.Session, day: date) -> list[Market]:
    ev = session.get(hkc.GAMMA_EVENTS_URL, params={"slug": hks.event_slug(day)}, timeout=20).json()
    ev = ev[0] if isinstance(ev, list) and ev else (ev if isinstance(ev, dict) else None)
    out = []
    for m in (ev or {}).get("markets", []):
        try:
            b = parse_label(m.get("groupItemTitle") or "")
        except ValueError:
            continue
        toks = m.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else (toks or [])
        if len(toks) == 2:
            out.append(Market(b, m["groupItemTitle"], str(m.get("id")), m.get("conditionId") or "", str(toks[0]), str(toks[1])))
    out.sort(key=lambda x: x.bucket.lo)
    return out


def book(session: requests.Session, token: str) -> tuple[list, list]:
    r = session.get(CLOB_BOOK, params={"token_id": token}, timeout=10)
    r.raise_for_status()
    d = r.json()
    asks = sorted((float(a["price"]), float(a["size"])) for a in d.get("asks", []))
    bids = sorted(((float(a["price"]), float(a["size"])) for a in d.get("bids", [])), reverse=True)
    return asks, bids


def fill(asks: list, usd: float, max_price: float, fee_rate: float = WEATHER_TAKER_FEE) -> dict:
    shares = cost = fee_total = 0.0
    left = usd
    for p, s in asks:
        if p > max_price or left <= 0:
            break
        fee = fee_rate * p * (1 - p)
        unit = p + fee
        take = min(s, left / unit)
        shares += take
        cost += take * unit
        fee_total += take * fee
        left -= take * unit
    return {"shares": shares, "cost": cost, "fee": fee_total, "best_ask": asks[0][0] if asks else None,
            "avg_price": (cost - fee_total) / shares if shares else None,
            "depth_shares": sum(s for p, s in asks if p <= max_price),
            "depth_usd": sum(p * s for p, s in asks if p <= max_price)}


def killed_by(markets: list[Market], new_whole: int) -> list[Market]:
    """Buckets that become impossible when the max-since-midnight reaches whole degree `new_whole`."""
    return [m for m in markets if m.bucket.hi == new_whole]


def containing(markets: list[Market], whole: int) -> Market | None:
    return next((m for m in markets if m.bucket.lo <= whole < m.bucket.hi), None)
