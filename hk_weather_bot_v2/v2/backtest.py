"""v2 no-peek backtest:  tune (training only) -> freeze (hash written) -> test (opened once).

Tuning has two layers so the training result itself is honest:
  * walk-forward inside training: for each month June..August, pick settings using only the
    months before it, then score that month. This shows how the *tuning recipe* does on days it
    did not tune on, before the real test is ever opened.
  * final settings: picked on all training days, then frozen.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from itertools import product

from hkbot.buckets import winning_bucket
from hkbot.scoring import brier, trade_pnl

from . import data
from .model import V2, History, final_probs, trades

PROB_GRID = dict(
    lookback=[20, 40],
    sigma_scale=[0.8, 1.0, 1.25],
    bias_fix=[True, False],
    gamma=[0.8, 1.0, 1.2, 1.4],
    w_model=[0.0, 0.15, 0.3, 0.5],
)
TRADE_GRID = dict(
    min_edge=[0.04, 0.07, 0.10],
    max_price=[0.6, 0.95],
    no_only=[False, True],
)
MIN_TRADES = 12          # a trade setting needs at least this many training trades to be chosen


def score(snaps, cfg: V2, hist: History):
    rows = []
    for s in snaps:
        p = final_probs(s, cfg, hist)                       # 1) probabilities locked
        if p is None:
            continue
        ideas = trades(s, p, cfg)
        win = winning_bucket(s.buckets, s._actual, cutoff=True)   # 2) only now open the answer
        if win is None:
            continue
        wi = s.buckets.index(win)
        pnl = sum(trade_pnl(side, px, cfg.stake / px, lab == win.label) for _, lab, side, px in ideas)
        rows.append(dict(day=s.day, winner=win.label, reading=s._actual,
                         p_bot=p[wi], p_mkt=s.market_p[wi],
                         brier_bot=brier(p, wi), brier_mkt=brier(s.market_p, wi),
                         ll_bot=-math.log(max(p[wi], 1e-4)), ll_mkt=-math.log(max(s.market_p[wi], 1e-4)),
                         trades="; ".join(f"{sd} {lb}@{px:.3f}" for _, lb, sd, px in ideas),
                         n_trades=len(ideas), pnl=pnl))
    return rows


def summary(rows):
    n = len(rows)
    if not n:
        return dict(days=0, brier_bot=math.nan, brier_mkt=math.nan, ll_bot=math.nan, ll_mkt=math.nan,
                    trades=0, wins=0, pnl=0.0)
    return dict(days=n,
                brier_bot=sum(r["brier_bot"] for r in rows) / n, brier_mkt=sum(r["brier_mkt"] for r in rows) / n,
                ll_bot=sum(r["ll_bot"] for r in rows) / n, ll_mkt=sum(r["ll_mkt"] for r in rows) / n,
                trades=sum(r["n_trades"] for r in rows), pnl=sum(r["pnl"] for r in rows),
                beat_days=sum(r["brier_bot"] < r["brier_mkt"] for r in rows))


def tune(snaps, hist):
    """Probabilities: lowest log loss (it punishes confident misses, unlike Brier).
    Trading: highest P&L with at least MIN_TRADES trades; if none qualifies -> no trading."""
    best_p, best_ll = V2(), math.inf
    for lb, ss, bf, g, w in product(*PROB_GRID.values()):
        cfg = V2(lookback=lb, sigma_scale=ss, bias_fix=bf, gamma=g, w_model=w)
        ll = summary(score(snaps, cfg, hist))["ll_bot"]
        if ll < best_ll - 1e-9:
            best_p, best_ll = cfg, ll
    best, best_pnl = replace(best_p, min_edge=9.9), 0.0          # 9.9 = never trade
    for e, mx, no in product(*TRADE_GRID.values()):
        cfg = replace(best_p, min_edge=e, max_price=mx, no_only=no)
        sm = summary(score(snaps, cfg, hist))
        if sm["trades"] >= MIN_TRADES and sm["pnl"] > best_pnl:
            best, best_pnl = cfg, sm["pnl"]
    return best


def walk_forward(train, hist):
    out = []
    for m in (6, 7, 8):
        fit = [s for s in train if s.day < date(2026, m, 1)]
        hold = [s for s in train if s.day.month == m]
        cfg = tune(fit, hist)
        out.append((m, cfg, summary(score(hold, cfg, hist)), score(hold, cfg, hist)))
    return out


def frozen_hash(cfgs: dict) -> str:
    return hashlib.sha256(json.dumps(cfgs, sort_keys=True, default=str).encode()).hexdigest()[:16]


def freeze(cfgs: dict, notes: dict):
    data.RESULTS.mkdir(exist_ok=True)
    blob = dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                settings=cfgs, sha=frozen_hash(cfgs), training_notes=notes,
                rule="Nothing in settings may change after this file exists. The test is read once.")
    data.FROZEN.write_text(json.dumps(blob, indent=2, default=str))
    return blob


def load_frozen():
    blob = json.loads(data.FROZEN.read_text())
    assert blob["sha"] == frozen_hash(blob["settings"]), "FROZEN_v2.json was edited after freezing"
    return {k: V2(**v) for k, v in blob["settings"].items()}, blob
