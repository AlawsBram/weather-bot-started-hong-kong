"""v2 probability model and trade rules.

What changes from v1 (each piece is one small knob, tuned on training days only):

1. Uncertainty from the bot's OWN recent misses. v1 guesses sigma from source spreads; v2 measures
   how far its combined guess actually missed over the last `lookback` known days (<= D-2) and
   uses that, times `sigma_scale`.
2. A recent-bias fix on the combined guess (same window): if it ran 0.6 C hot lately, take 0.6 off.
3. Market sharpening: market prices are raised to the power `gamma` and re-normalised. gamma > 1
   trusts the favourite more, gamma < 1 trusts long shots more. This tests for the classic
   "long shots are over-priced" habit of betting markets.
4. Geometric pooling instead of v1's straight average: p ~ market^(1-w) * model^w. A straight
   average lets a 2-cent market bucket jump to 10 cents when the model likes it; geometric pooling
   needs both to agree, which is kinder to the market in the tails.
5. Trades only where the edge clears `min_edge` AFTER spread and the 5% taker fee, priced between
   `min_price` and `max_price`, optionally `no_only` (only bet against buckets).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import timedelta

from hkbot.config import get_strategy
from hkbot.model import Forecast, bucket_probs, combine

BASE = get_strategy("v2_audited")
TAIL = 0.002


@dataclass(frozen=True)
class V2:
    lookback: int = 30
    sigma_scale: float = 1.0
    sigma_floor: float = 0.6
    bias_fix: bool = True
    gamma: float = 1.0
    w_model: float = 0.0
    min_edge: float = 0.06
    min_price: float = 0.05
    max_price: float = 0.95
    no_only: bool = False
    max_trades: int = 2
    stake: float = 10.0

    def key(self) -> str:
        return (f"lb{self.lookback} ss{self.sigma_scale} bias{int(self.bias_fix)} g{self.gamma} "
                f"w{self.w_model} e{self.min_edge} px{self.min_price}-{self.max_price} no{int(self.no_only)}")


def _norm(p):
    s = sum(p)
    return [x / s for x in p] if s > 0 else p


class History:
    """The bot's own combined guess per day, so day D can learn from days <= D-2 only."""

    def __init__(self, snaps):
        self.guess = {}
        for s in snaps:
            fc = combine(s.forecasts, BASE, s.priors)
            if fc is not None:
                self.guess[s.day] = (fc, s._actual)        # actual used ONLY for days <= D-2 below

    def recent(self, day, lookback):
        lo, hi = day - timedelta(days=lookback + 2), day - timedelta(days=2)
        return [g.mean - a for d, (g, a) in self.guess.items() if lo <= d <= hi]


def model_probs(snap, cfg: V2, hist: History):
    item = hist.guess.get(snap.day)
    if item is None:
        return None
    fc = item[0]
    errs = hist.recent(snap.day, cfg.lookback)
    mean, sigma = fc.mean, fc.sigma
    if len(errs) >= 8:
        bias = sum(errs) / len(errs) if cfg.bias_fix else 0.0
        rms = math.sqrt(sum((e - bias) ** 2 for e in errs) / (len(errs) - 1))
        mean, sigma = mean - bias, rms
    sigma = max(cfg.sigma_floor, sigma * cfg.sigma_scale)
    f2 = Forecast(mean, sigma, {k: v - (fc.mean - mean) for k, v in fc.corrected.items()}, fc.weights)
    strat = replace(BASE, plausible_floor=0.0, tail_floor=TAIL)
    return bucket_probs(snap.buckets, f2, strat, snap.obs_max, snap.hours_left)


def final_probs(snap, cfg: V2, hist: History):
    q = model_probs(snap, cfg, hist)
    if q is None:
        return None
    m = _norm([max(x, TAIL) ** cfg.gamma for x in snap.market_p])
    if cfg.w_model == 0:
        p = m
    else:
        p = _norm([mi ** (1 - cfg.w_model) * max(qi, TAIL) ** cfg.w_model for mi, qi in zip(m, q)])
    # buckets the live reading has already ruled out are impossible, whatever the market says
    if snap.obs_max is not None:
        p = _norm([0.0 if b.hi <= snap.obs_max else x for b, x in zip(snap.buckets, p)])
    return p


FEE = 0.05
SPREAD = 0.02


def costs(mid):
    """Historical price is a mid: YES costs mid+spread+fee, NO costs 1-(mid-spread)+fee."""
    yes = min(0.999, mid + SPREAD)
    no = min(0.999, 1 - max(0.001, mid - SPREAD))
    return yes + FEE * yes * (1 - yes), no + FEE * no * (1 - no)


def trades(snap, probs, cfg: V2):
    ideas = []
    for b, mid, p in zip(snap.buckets, snap.market_raw, probs):
        if mid is None:
            continue
        yes_cost, no_cost = costs(mid)
        if not cfg.no_only and cfg.min_price <= yes_cost <= cfg.max_price and p - yes_cost >= cfg.min_edge:
            ideas.append((p - yes_cost, b.label, "YES", yes_cost))
        if cfg.min_price <= no_cost <= cfg.max_price and (1 - p) - no_cost >= cfg.min_edge:
            ideas.append(((1 - p) - no_cost, b.label, "NO", no_cost))
    ideas.sort(reverse=True)
    return ideas[: cfg.max_trades]
