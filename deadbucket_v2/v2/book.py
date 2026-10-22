"""Polymarket CLOB order book for a NO token, and what a taker buy really costs."""
from __future__ import annotations

import requests

CLOB = "https://clob.polymarket.com"
WEATHER_TAKER_FEE = 0.05          # fee per share = rate * p * (1 - p)   (weather fee schedule)


def no_asks(session: requests.Session, no_token: str, timeout: int = 10) -> list[tuple[float, float]]:
    """[(price, size)] cheapest first, for buying NO."""
    r = session.get(f"{CLOB}/book", params={"token_id": no_token}, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    asks = [(float(a["price"]), float(a["size"])) for a in d.get("asks", [])]
    return sorted(asks)


def fill(asks: list[tuple[float, float]], usd: float, max_price: float, fee_rate: float = WEATHER_TAKER_FEE) -> dict:
    """Walk the book spending up to `usd` on levels priced <= max_price.
    -> shares, cost (incl. fee), avg price, fee, best ask, depth(shares) at or under max_price."""
    shares = cost = fee_total = 0.0
    left = usd
    depth = sum(s for p, s in asks if p <= max_price)
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
    return {"shares": shares, "cost": cost, "avg_price": (cost - fee_total) / shares if shares else None,
            "fee": fee_total, "best_ask": asks[0][0] if asks else None, "depth_shares": depth}
