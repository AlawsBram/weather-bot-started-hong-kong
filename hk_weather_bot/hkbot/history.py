"""No-peek historical backtest on real past Polymarket Hong Kong markets.

For every past day D we rebuild ONLY what was knowable at a fixed decision time:

  evening  = 18:00 HKT on D-1   (models: forecast issued 2 days earlier — conservative)
  morning  = 09:00 HKT on D     (models: forecast issued 1 day earlier)

  - model forecasts AS ISSUED back then   (Open-Meteo Previous Runs API)
  - each bucket's Polymarket price AT the decision time (CLOB price history)
  - source biases learned only from days <= D-2 (D-1's reading isn't published yet)

Only after the probabilities are locked do we look up the HKO reading (the answer)
and score them. Settings are tuned on a TRAINING period, then frozen and judged on
a later TEST period the tuner never sees.

Added 2026-10-21 (see hkarchive.py), each switchable so its effect can be measured alone:
  - HKO's OWN forecast as issued then (archived hourly Local Weather Forecast bulletin)   --no-hko
  - the live HKO "max since midnight" as it read at the decision time (archived CSV)     --no-obs
  - decision times "noon" (12:00) and "afternoon" (14:00) on the day, where live readings bite
  - the answer from CLMMAXT + Daily Extract + HKO's daily report (RYES), so October counts
  - the 5% weather taker fee on every paper trade (--fee)
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path

from . import config, sources
from .buckets import Bucket, parse_label, winning_bucket
from .config import Strategy
from .model import blend_with_market, bucket_probs, combine, priors_from_stats, source_stats
from .scoring import brier, log_loss, trade_pnl
from .sources import HKT, MarketBucket, _f, _json_list
from .strategy import find_trades

log = logging.getLogger(__name__)

DECISIONS = {
    # name: (days before D, local time, Previous-Runs lead in days)
    "evening": (1, time(18, 0), 2),
    "morning": (0, time(9, 0), 1),
    "noon": (0, time(12, 0), 1),
    "afternoon": (0, time(14, 0), 1),
}
PEAK_HOUR = 17.0           # same assumption as the live bot: heating stops around 17:00 HKT
BIAS_LAG_DAYS = 2          # on day D we only know readings up to D-2 for sure
BIAS_LOOKBACK_DAYS = 30


def decision_dt(d: date, decision: str) -> datetime:
    back, t, _ = DECISIONS[decision]
    return datetime.combine(d - timedelta(days=back), t, tzinfo=HKT)


# ----------------------------------------------------------------------------
# Disk cache — reruns are fast and you can inspect exactly what was fetched
# ----------------------------------------------------------------------------

class Cache:
    def __init__(self, root: Path, refresh: bool = False):
        self.root = root / "cache"
        self.root.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh

    def get(self, url: str, params: dict):
        return self._get(url, params, as_text=False)

    def get_text(self, url: str, params: dict):
        """Same, for answers that are not JSON (archived XML / CSV files)."""
        return self._get(url, params, as_text=True)

    def _get(self, url: str, params: dict, as_text: bool):
        key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
        path = self.root / f"{key}.json"
        if path.exists() and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        if "data.gov.hk" in url or "weather.gov.hk" in url:
            from .hkarchive import polite_pause
            polite_pause()
        r = sources._get(url, params)
        if r is None:
            return None
        try:
            data = r.content.decode("utf-8-sig") if as_text else r.json()
        except (ValueError, UnicodeDecodeError):
            return None
        path.write_text(json.dumps({"url": url, "params": params, "data": data}), encoding="utf-8")
        return {"url": url, "params": params, "data": data}


def _data(blob):
    return None if blob is None else blob.get("data")


# ----------------------------------------------------------------------------
# Fetchers
# ----------------------------------------------------------------------------

def parse_previous_runs(payload: dict, lead: int) -> dict[date, float]:
    """Hourly 'temperature_2m_previous_dayN' -> daily max per HK calendar day."""
    hourly = (payload or {}).get("hourly", {})
    prefix = f"temperature_2m_previous_day{lead}"
    key = next((k for k in hourly if k == prefix or k.startswith(prefix + "_")), None)
    if key is None:
        return {}
    out: dict[date, float] = {}
    counts: dict[date, int] = {}
    for t, v in zip(hourly.get("time", []), hourly.get(key, [])):
        if v is None:
            continue
        d = datetime.fromisoformat(t).date()
        out[d] = max(out.get(d, -99.0), float(v))
        counts[d] = counts.get(d, 0) + 1
    # need most of the day's hours, incl. the afternoon peak
    return {d: v for d, v in out.items() if counts[d] >= 18}


def fetch_previous_runs(cache: Cache, start: date, end: date, lead: int) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for model_id, name in config.MODEL_SOURCES.items():
        # chunk by ~2 months to keep responses small
        series: dict[date, float] = {}
        s = start
        while s <= end:
            e = min(end, s + timedelta(days=60))
            blob = cache.get(config.OPEN_METEO_PREVIOUS_RUNS, {
                "latitude": config.HKO_LAT, "longitude": config.HKO_LON,
                "hourly": f"temperature_2m_previous_day{lead}", "models": model_id,
                "timezone": config.HK_TZ, "start_date": s.isoformat(), "end_date": e.isoformat(),
            })
            series.update(parse_previous_runs(_data(blob), lead))
            s = e + timedelta(days=1)
        if series:
            out[name] = series
        else:
            log.warning("No previous-run data for %s", model_id)
    return out


def fetch_actuals(cache: Cache, start: date, end: date) -> dict[date, float]:
    """The official HKO daily max. CLMMAXT (filled in weeks later), then the Daily Extract
    data file (about a day later), then HKO's daily report RYES (01:30 HKT next day) for any
    day still missing. All three gave identical numbers wherever they overlapped."""
    from .hkarchive import ryes_max
    out: dict[date, float] = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        blob = cache.get(config.HKO_OPENDATA_CLIMATE, {
            "dataType": "CLMMAXT", "rformat": "json", "station": "HKO", "year": y, "month": m})
        out.update(sources.parse_clmmaxt(_data(blob)))
        ext = cache.get_text(config.HKO_DAILY_EXTRACT.format(yyyymm=f"{y}{m:02d}"), {})
        try:
            for d, v in sources.parse_daily_extract(json.loads(_data(ext) or "{}"), y).items():
                out.setdefault(d, v)
        except ValueError:
            pass
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = start
    while d <= end:
        if d not in out:
            v = ryes_max(cache, d)
            if v is not None:
                out[d] = v
        d += timedelta(days=1)
    return {d: v for d, v in out.items() if start <= d <= end}


def price_at(history: list[dict], ts: int) -> float | None:
    """Last traded/marked price at or BEFORE ts. Points after ts are never used."""
    best_t, best_p = None, None
    for pt in history or []:
        t, p = pt.get("t"), _f(pt.get("p"))
        if t is None or p is None or t > ts:
            continue
        if best_t is None or t > best_t:
            best_t, best_p = t, p
    # stale guard: ignore a price older than 36 h before the decision
    if best_t is not None and ts - best_t > 36 * 3600:
        return None
    return best_p


@dataclass
class HistMarket:
    day: date
    slug: str
    buckets: list[Bucket]
    prices: list[float | None]     # YES price at decision time, per bucket
    winner_label: str | None       # Polymarket's settled winner (for a rule check only)


def fetch_market_history(cache: Cache, d: date, decision: str) -> HistMarket | None:
    slug = sources.event_slug(d)
    events = _data(cache.get(config.GAMMA_EVENTS_URL, {"slug": slug}))
    if isinstance(events, dict):
        events = [events]
    if not events:
        return None
    ev = events[0]
    ts = int(decision_dt(d, decision).timestamp())
    rows = []
    for m in ev.get("markets", []):
        label = m.get("groupItemTitle") or m.get("question", "")
        try:
            b = parse_label(label)
        except ValueError:
            continue
        tokens = _json_list(m.get("clobTokenIds"))
        price = None
        if tokens:
            hist = _data(cache.get(config.CLOB_PRICES_HISTORY, {
                "market": tokens[0], "startTs": ts - 3 * 86400, "endTs": ts, "fidelity": 60}))
            pts = (hist or {}).get("history", [])
            if not pts:   # some closed markets only answer to interval queries
                hist = _data(cache.get(config.CLOB_PRICES_HISTORY, {
                    "market": tokens[0], "interval": "max", "fidelity": 60}))
                pts = (hist or {}).get("history", [])
            price = price_at(pts, ts)
        final = [_f(p) for p in _json_list(m.get("outcomePrices"))]
        won = bool(m.get("closed")) and bool(final) and final[0] == 1.0
        rows.append((b, price, won))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0].lo)
    winners = [r[0].label for r in rows if r[2]]
    return HistMarket(d, slug, [r[0] for r in rows], [r[1] for r in rows],
                      winners[0] if len(winners) == 1 else None)


# ----------------------------------------------------------------------------
# Snapshot per day (everything the model may see) + the sealed answer
# ----------------------------------------------------------------------------

@dataclass
class DaySnapshot:
    day: date
    buckets: list[Bucket]
    market_raw: list[float | None]
    market_p: list[float]            # normalised to sum 1
    forecasts: dict[str, float]      # as issued before the decision
    priors: dict                     # learned from days <= D-2 only
    obs_max: float | None = None     # HKO max since midnight as it read at the decision time
    hours_left: float | None = None  # hours of heating left at the decision time
    # --- sealed: only read by score_day() ---
    _actual: float = field(repr=False, default=math.nan)
    _pm_winner: str | None = field(repr=False, default=None)


def lagged_stats(day: date, actuals: dict[date, float], fcsts: dict[str, dict[date, float]]) -> dict:
    cutoff = day - timedelta(days=BIAS_LAG_DAYS)
    first = day - timedelta(days=BIAS_LOOKBACK_DAYS + BIAS_LAG_DAYS)
    stats = {}
    for src, series in fcsts.items():
        errs = [series[x] - actuals[x] for x in series if x in actuals and first <= x <= cutoff]
        stats[src] = source_stats(errs)
    return stats


def build_snapshots(markets: dict[date, HistMarket], fcsts, actuals, obs: dict | None = None,
                    decision: str | None = None) -> tuple[list[DaySnapshot], list[str]]:
    snaps, skipped = [], []
    obs = obs or {}
    for d in sorted(markets):
        hm = markets[d]
        if d not in actuals:
            skipped.append(f"{d}: no HKO reading")
            continue
        raw = {s: series[d] for s, series in fcsts.items() if d in series}
        if not raw:
            skipped.append(f"{d}: no as-issued model forecasts")
            continue
        priced = [p for p in hm.prices if p is not None]
        if len(priced) < 3 or sum(priced) < 0.6:
            skipped.append(f"{d}: market prices missing at decision time")
            continue
        tot = sum(priced)
        mp = [(p or 0.0) / tot for p in hm.prices]
        hours_left = None
        if decision and DECISIONS[decision][0] == 0:
            t = DECISIONS[decision][1]
            hours_left = max(0.0, PEAK_HOUR - (t.hour + t.minute / 60))
        snaps.append(DaySnapshot(d, hm.buckets, hm.prices, mp, raw,
                                 priors_from_stats(lagged_stats(d, actuals, fcsts)),
                                 obs_max=obs.get(d), hours_left=hours_left,
                                 _actual=actuals[d], _pm_winner=hm.winner_label))
    return snaps, skipped


# ----------------------------------------------------------------------------
# Predict -> lock -> score
# ----------------------------------------------------------------------------

@dataclass
class DayScore:
    day: date
    winner: str
    reading: float
    rule_ok: bool | None
    mean: float
    sigma: float
    p_bot: float
    p_mkt: float
    brier_bot: float
    brier_mkt: float
    trades: list = field(default_factory=list)   # (label, side, price, edge, pnl)

    @property
    def pnl(self) -> float:
        return sum(t[4] for t in self.trades)


def predict(snap: DaySnapshot, strat: Strategy) -> tuple[list[float], object] | None:
    fc = combine(snap.forecasts, strat, snap.priors)
    if fc is None:
        return None
    model_p = bucket_probs(snap.buckets, fc, strat, snap.obs_max, snap.hours_left)
    return blend_with_market(model_p, snap.market_p, strat.market_blend), fc


FEE_RATE = 0.05     # weather markets: taker fee = rate * p * (1 - p) per share (gamma feeSchedule)


def sim_market(snap: DaySnapshot, spread: float, fee_rate: float | None = None) -> list[MarketBucket]:
    """Historical price is a MID. Buying YES costs mid + spread + fee; buying NO costs
    (1 - (mid - spread)) + fee. The fee is folded into the prices, so trade decisions AND P&L pay it."""
    fr = FEE_RATE if fee_rate is None else fee_rate
    out = []
    for b, p in zip(snap.buckets, snap.market_raw):
        if p is None:
            out.append(MarketBucket(b, None, None, None, None))
        else:
            ask = min(0.999, p + spread)
            no = min(0.999, 1 - max(0.001, p - spread))
            ask_all_in = min(0.999, ask + fr * ask * (1 - ask))
            no_all_in = min(0.999, no + fr * no * (1 - no))
            out.append(MarketBucket(b, 1 - no_all_in, ask_all_in, p, None))
    return out


def score_day(snap: DaySnapshot, strat: Strategy, spread: float) -> DayScore | None:
    pred = predict(snap, strat)                  # 1) probabilities locked first
    if pred is None:
        return None
    probs, fc = pred
    ideas = find_trades(sim_market(snap, spread), probs, strat)
    win = winning_bucket(snap.buckets, snap._actual, cutoff=True)   # 2) only now open the answer
    if win is None:
        return None
    wi = snap.buckets.index(win)
    rule_ok = None if snap._pm_winner is None else (snap._pm_winner == win.label)
    trades = []
    for t in ideas:
        pnl = trade_pnl(t.side, t.price, t.shares, t.label == win.label)
        trades.append((t.label, t.side, t.price, t.edge, pnl))
    return DayScore(snap.day, win.label, snap._actual, rule_ok, fc.mean, fc.sigma,
                    probs[wi], snap.market_p[wi], brier(probs, wi), brier(snap.market_p, wi), trades)


def evaluate(snaps: list[DaySnapshot], strat: Strategy, spread: float) -> list[DayScore]:
    return [s for s in (score_day(x, strat, spread) for x in snaps) if s is not None]


def summary(scores: list[DayScore]) -> dict:
    n = len(scores)
    if not n:
        return {"days": 0}
    trades = [t for s in scores for t in s.trades]
    return {
        "days": n,
        "brier_bot": sum(s.brier_bot for s in scores) / n,
        "brier_mkt": sum(s.brier_mkt for s in scores) / n,
        "beat_mkt_days": sum(s.brier_bot < s.brier_mkt for s in scores),
        "p_win_bot": sum(s.p_bot for s in scores) / n,
        "p_win_mkt": sum(s.p_mkt for s in scores) / n,
        "trades": len(trades),
        "winning_trades": sum(t[4] > 0 for t in trades),
        "pnl": sum(t[4] for t in trades),
    }


# ----------------------------------------------------------------------------
# Tuning (training days ONLY) -> freeze -> test
# ----------------------------------------------------------------------------

PROB_GRID = {
    "sigma_scale": [0.7, 0.85, 1.0, 1.15, 1.3, 1.5],
    "market_blend": [0.0, 0.25, 0.5, 0.75, 1.0],
}
EDGE_GRID = [0.04, 0.06, 0.08, 0.10, 0.15]
MIN_TRAIN_TRADES = 10


def tune(train: list[DaySnapshot], base: Strategy, spread: float) -> tuple[Strategy, list]:
    log_rows = []
    best, best_b = base, math.inf
    for sc, mb in product(PROB_GRID["sigma_scale"], PROB_GRID["market_blend"]):
        s = replace(base, sigma_scale=sc, market_blend=mb)
        summ = summary(evaluate(train, s, spread))
        if summ["days"]:
            log_rows.append(("prob", sc, mb, None, summ["brier_bot"], summ["pnl"], summ["trades"]))
            if summ["brier_bot"] < best_b:
                best, best_b = s, summ["brier_bot"]
    # edge threshold chosen on training P&L, with a minimum sample of trades
    best_e, best_pnl = best.min_edge, -math.inf
    for e in EDGE_GRID:
        s = replace(best, min_edge=e)
        summ = summary(evaluate(train, s, spread))
        log_rows.append(("edge", best.sigma_scale, best.market_blend, e, summ.get("brier_bot"),
                         summ.get("pnl"), summ.get("trades")))
        if summ.get("trades", 0) >= MIN_TRAIN_TRADES and summ["pnl"] > best_pnl:
            best_e, best_pnl = e, summ["pnl"]
    return replace(best, min_edge=best_e, version=base.version + "_tuned"), log_rows


@dataclass
class HistoryReport:
    decision: str
    train_days: list[date]
    test_days: list[date]
    tuned: Strategy
    rows: dict[str, dict]            # name -> {"train": summary, "test": summary}
    test_scores: dict[str, list[DayScore]]
    tune_log: list
    skipped: list[str]
    rule_mismatches: list[str]


def run_history(snaps: list[DaySnapshot], skipped: list[str], train_end: date, decision: str,
                spread: float, base: Strategy) -> HistoryReport:
    train = [s for s in snaps if s.day <= train_end]
    test = [s for s in snaps if s.day > train_end]
    tuned, tune_log = tune(train, base, spread)
    variants = {
        "tuned (frozen)": tuned,
        "model only": replace(tuned, market_blend=1.0),
        "untuned v2": base,
    }
    rows, test_scores = {}, {}
    for name, st in variants.items():
        tr, te = evaluate(train, st, spread), evaluate(test, st, spread)
        rows[name] = {"train": summary(tr), "test": summary(te)}
        test_scores[name] = te
    mism = [f"{s.day}: PM {s._pm_winner} vs cut-off {winning_bucket(s.buckets, s._actual).label} "
            f"(HKO {s._actual:.1f})"
            for s in snaps if s._pm_winner and winning_bucket(s.buckets, s._actual)
            and s._pm_winner != winning_bucket(s.buckets, s._actual).label]
    return HistoryReport(decision, [s.day for s in train], [s.day for s in test], tuned, rows,
                         test_scores, tune_log, skipped, mism)


def write_csv(path: Path, scores: list[DayScore]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "hko_reading", "winner", "rule_ok", "guess", "sigma", "p_winner_bot",
                    "p_winner_mkt", "brier_bot", "brier_mkt", "trades", "pnl"])
        for s in scores:
            w.writerow([s.day, s.reading, s.winner, s.rule_ok, round(s.mean, 2), round(s.sigma, 2),
                        round(s.p_bot, 4), round(s.p_mkt, 4), round(s.brier_bot, 4), round(s.brier_mkt, 4),
                        "; ".join(f"{t[1]} {t[0]}@{t[2]:.3f}" for t in s.trades), round(s.pnl, 2)])


# ----------------------------------------------------------------------------
# Glue: fetch everything for a date range
# ----------------------------------------------------------------------------

def load_range(start: date, end: date, decision: str, refresh: bool = False, progress=print,
               use_hko: bool = True, use_obs: bool = True):
    from . import hkarchive
    cache = Cache(config.HISTORY_DIR, refresh)
    lead = DECISIONS[decision][2]
    warm = start - timedelta(days=BIAS_LOOKBACK_DAYS + BIAS_LAG_DAYS + 2)
    progress(f"Fetching HKO readings {warm} .. {end}")
    actuals = fetch_actuals(cache, warm, end)
    progress(f"Fetching as-issued model forecasts (lead {lead} day)")
    fcsts = fetch_previous_runs(cache, warm, end, lead)
    obs: dict[date, float] = {}
    if use_hko:
        progress("Fetching HKO's own forecast as issued at the decision time (data.gov.hk archive)")
        hko: dict[date, float] = {}
        d = warm
        while d <= end:
            v, _ = hkarchive.hko_forecast_at(cache, d, decision_dt(d, decision))
            if v is not None:
                hko[d] = v
            d += timedelta(days=1)
        if hko:
            fcsts["hko_official"] = hko
    if use_obs and DECISIONS[decision][0] == 0:
        progress("Fetching HKO max-since-midnight as it read at the decision time (data.gov.hk archive)")
        d = start
        while d <= end:
            v, _ = hkarchive.max_so_far_at(cache, d, decision_dt(d, decision))
            if v is not None:
                obs[d] = v
            d += timedelta(days=1)
    progress(f"Fetching Polymarket prices at decision time ({decision})")
    markets: dict[date, HistMarket] = {}
    d = start
    while d <= end:
        hm = fetch_market_history(cache, d, decision)
        if hm:
            markets[d] = hm
        d += timedelta(days=1)
    progress(f"  readings: {len(actuals)} | sources: "
             + (", ".join(f"{k} {len(v)}d" for k, v in fcsts.items()) or "none")
             + f" | live max-so-far: {len(obs)}d | markets: {len(markets)}")
    return markets, fcsts, actuals, obs
