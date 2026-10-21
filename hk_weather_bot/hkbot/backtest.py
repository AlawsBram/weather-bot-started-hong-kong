"""Walk-forward backtest and calibration.

For each past day, the model only uses errors from days BEFORE it (no peeking),
predicts bucket probabilities, and is scored against the real HKO reading using
the cut-off rule. `calibrate` searches for the uncertainty scale that gives the
lowest average Brier score — i.e. it keeps adjusting until the probabilities
line up best with what actually happened.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from . import config, sources
from .buckets import Bucket, winning_bucket
from .config import Strategy
from .model import bucket_probs, combine, priors_from_stats, source_stats
from .scoring import brier, log_loss


@dataclass
class DayResult:
    day: date
    actual: float
    winner: str
    mean: float
    sigma: float
    p_winner: float
    brier: float
    logloss: float
    top_pick: str


def generic_buckets(center: int, width: int = 6) -> list[Bucket]:
    """Same shape as Polymarket's HK ladder: 1-degree buckets with open-ended ends."""
    lo, hi = center - width, center + width
    out = [Bucket(f"{lo}°C or below", -math.inf, lo + 1)]
    out += [Bucket(f"{k}°C", k, k + 1) for k in range(lo + 1, hi)]
    out.append(Bucket(f"{hi}°C or higher", hi, math.inf))
    return out


def logged_hko_forecasts(con, start: date, end: date, source: str = "hko_official") -> dict[date, float]:
    """Forecasts of `source` the bot logged on the day BEFORE each target (1-day lead, latest one)."""
    out: dict[date, float] = {}
    rows = con.execute(
        "SELECT run_ts, target_date, value FROM forecasts WHERE source=? "
        "AND target_date BETWEEN ? AND ? ORDER BY run_ts",
        (source, start.isoformat(), end.isoformat()),
    ).fetchall()
    for r in rows:
        tgt = date.fromisoformat(r["target_date"])
        run_day = datetime.fromisoformat(r["run_ts"]).date()
        if run_day < tgt:            # keep the latest pre-day forecast
            out[tgt] = r["value"]
    return out


def gather(con, days: int, end: date | None = None):
    end = end or (sources.today_hkt() - timedelta(days=1))
    start = end - timedelta(days=days + 30)    # +30 warm-up days for bias estimates
    actual = sources.fetch_hko_daily_max_range(start, end)
    models = sources.fetch_model_forecasts(start, end, historical=True)
    hko = logged_hko_forecasts(con, start, end)
    if hko:
        models["hko_official"] = hko
    # Hand-logged sources (e.g. google_manual) get scored too, so you can see if they help.
    for (src,) in con.execute("SELECT DISTINCT source FROM forecasts WHERE source LIKE '%_manual'"):
        series = logged_hko_forecasts(con, start, end, src)
        if series:
            models[src] = series
    return start, end, actual, models


def walk_forward(strat: Strategy, actual: dict[date, float], fcsts: dict[str, dict[date, float]],
                 test_days: list[date], lookback: int = 30) -> list[DayResult]:
    results = []
    for d in test_days:
        if d not in actual:
            continue
        stats = {}
        for src, series in fcsts.items():
            errs = [series[x] - actual[x] for x in series
                    if x in actual and d - timedelta(days=lookback) <= x < d]
            stats[src] = source_stats(errs)
        priors = priors_from_stats(stats)
        raw = {src: series[d] for src, series in fcsts.items() if d in series}
        fc = combine(raw, strat, priors)
        if fc is None:
            continue
        buckets = generic_buckets(int(round(fc.mean)))
        probs = bucket_probs(buckets, fc, strat)
        win = winning_bucket(buckets, actual[d], cutoff=True)   # the REAL rule, always
        wi = buckets.index(win)
        top = max(range(len(probs)), key=probs.__getitem__)
        results.append(DayResult(d, actual[d], win.label, fc.mean, fc.sigma, probs[wi],
                                 brier(probs, wi), log_loss(probs, wi), buckets[top].label))
    return results


def summarise(results: list[DayResult]) -> dict:
    n = len(results)
    if n == 0:
        return {"days": 0}
    return {
        "days": n,
        "avg_brier": round(sum(r.brier for r in results) / n, 4),
        "avg_logloss": round(sum(r.logloss for r in results) / n, 4),
        "avg_p_winner": round(sum(r.p_winner for r in results) / n, 4),
        "top_pick_hit_rate": round(sum(r.top_pick == r.winner for r in results) / n, 4),
    }


def calibrate(strat: Strategy, actual, fcsts, test_days, grid=None) -> tuple[float, list[tuple[float, dict]]]:
    grid = grid or [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6]
    table = []
    for s in grid:
        summ = summarise(walk_forward(replace(strat, sigma_scale=s), actual, fcsts, test_days))
        table.append((s, summ))
    scored = [t for t in table if t[1].get("days")]
    best = min(scored, key=lambda t: t[1]["avg_brier"])[0] if scored else 1.0
    return best, table


def save_calibration(actual, fcsts, end: date, sigma_scale: float, lookback: int = 45) -> dict:
    stats = {}
    for src, series in fcsts.items():
        errs = [series[x] - actual[x] for x in series if x in actual and end - timedelta(days=lookback) < x <= end]
        stats[src] = source_stats(errs)
    cal = {"updated": datetime.now().isoformat(timespec="seconds"), "through": end.isoformat(),
           "sigma_scale": sigma_scale, "sources": stats}
    config.CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CALIBRATION_PATH.write_text(json.dumps(cal, indent=2))
    return cal
