"""Bilberry "forecast-change" rule, backtested on Hong Kong (Idea 05 from the X search).

The rule is taken word for word from the post (x.com/xmayeth/status/2108586474715185534) and
NOT tuned, so every day from 2026-04-15 is out-of-sample:
  * when a new model run is out, compare tomorrow's max with the previous run of the same model
  * if it moved by >= 0.5 C, buy the bucket ONE degree closer to the new forecast
    (one step from the bucket the market favours right now, in the direction of the move)
  * only if that bucket costs 5c..60c; at most $25 a trade

No peeking:
  * a run counts only from its PUBLIC time = init + delay measured from Open-Meteo's own
    metadata on 2026-10-22 (ECMWF 7.8 h, GFS 6.4 h) + 5 min for our bot to react
  * the buy uses the first 5-minute price point AT OR AFTER that moment (never before)
  * the answer (HKO daily max) is opened only to settle trades that are already fixed

Costs: half of a 2c spread on every buy and sell + taker fee 0.05*p*(1-p) per share.
"""
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
V1 = HERE.parent / "hk_weather_bot"
sys.path.insert(0, str(V1))

from hkbot import config, sources  # noqa: E402
from hkbot import history as H  # noqa: E402
from hkbot.buckets import parse_label, cutoff_value  # noqa: E402

HKT = timezone(timedelta(hours=8))
START, END = date(2026, 4, 15), date(2026, 10, 20)
SINGLE_RUNS = "https://single-runs-api.open-meteo.com/v1/forecast"
CLOB_HIST = config.CLOB_PRICES_HISTORY
MODELS = {"ecmwf_ifs025": 7.8, "gfs_seamless": 6.4}        # public delay in hours (measured)
REACT_MIN = 5
MIN_MOVE, P_LO, P_HI, STAKE = 0.5, 0.05, 0.60, 25.0
HALF_SPREAD, FEE = 0.01, 0.05
EXTRA_DELAY_H = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0   # robustness: pretend runs come out later

cache = H.Cache(HERE / "data")


def run_max(model: str, init: datetime) -> dict[date, float]:
    """Daily max (HKT calendar day) for every full day in one model run."""
    blob = cache.get(SINGLE_RUNS, {"latitude": config.HKO_LAT, "longitude": config.HKO_LON,
                                   "hourly": "temperature_2m", "models": model, "forecast_days": 4,
                                   "run": init.strftime("%Y-%m-%dT%H:%M"), "timezone": config.HK_TZ})
    h = (H._data(blob) or {}).get("hourly", {})
    days, cnt = {}, defaultdict(int)
    for t, v in zip(h.get("time", []), h.get("temperature_2m", [])):
        if v is None:
            continue
        d = datetime.fromisoformat(t).date()
        days[d] = max(days.get(d, -99), v)
        cnt[d] += 1
    return {d: v for d, v in days.items() if cnt[d] >= 18}


@dataclass
class Market:
    day: date
    labels: list
    buckets: list
    series: list          # per bucket: sorted [(ts, price)]
    winner: int | None


def market(d: date) -> Market | None:
    ev = H._data(cache.get(config.GAMMA_EVENTS_URL, {"slug": sources.event_slug(d)}))
    ev = ev[0] if isinstance(ev, list) and ev else ev
    if not ev:
        return None
    rows = []
    start = int(datetime.combine(d - timedelta(days=4), time(0), HKT).timestamp())
    end = int(datetime.combine(d + timedelta(days=1), time(0), HKT).timestamp())
    for m in ev.get("markets", []):
        label = m.get("groupItemTitle") or ""
        try:
            b = parse_label(label)
        except ValueError:
            continue
        tok = H._json_list(m.get("clobTokenIds"))
        if not tok:
            continue
        hist = H._data(cache.get(CLOB_HIST, {"market": tok[0], "startTs": start, "endTs": end, "fidelity": 5}))
        pts = sorted((p["t"], float(p["p"])) for p in (hist or {}).get("history", []))
        final = [float(x) for x in H._json_list(m.get("outcomePrices")) or []]
        rows.append((b, label, pts, bool(m.get("closed")) and final[:1] == [1.0]))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0].lo)
    win = [i for i, r in enumerate(rows) if r[3]]
    return Market(d, [r[1] for r in rows], [r[0] for r in rows], [r[2] for r in rows], win[0] if len(win) == 1 else None)


def price_after(pts, ts):
    """First price point at or after ts (within 30 min) - a price we could actually have traded at."""
    for t, p in pts:
        if t >= ts:
            return (t, p) if t - ts <= 1800 else None
    return None


def price_before(pts, ts):
    best = None
    for t, p in pts:
        if t > ts:
            break
        best = (t, p)
    return best if best and ts - best[0] <= 3 * 3600 else None


def buy_cost(p):
    q = p + HALF_SPREAD
    return q + FEE * q * (1 - q)


def sell_value(p):
    q = max(0.0, p - HALF_SPREAD)
    return q - FEE * q * (1 - q)


def main():
    actuals = H.fetch_actuals(H.Cache(config.HISTORY_DIR), START, END)
    markets = {}
    d = START
    while d <= END:
        m = market(d)
        if m and m.winner is not None:
            markets[d] = m
        d += timedelta(days=1)
    print(f"markets with a settled winner: {len(markets)}", flush=True)

    trades = []
    for model, delay in MODELS.items():
        init = datetime.combine(START - timedelta(days=3), time(0), timezone.utc)
        prev = None
        while init <= datetime.combine(END, time(18), timezone.utc):
            cur = run_max(model, init)
            if prev and cur:
                public = init + timedelta(hours=delay + EXTRA_DELAY_H, minutes=REACT_MIN)
                tomorrow = public.astimezone(HKT).date() + timedelta(days=1)
                mk = markets.get(tomorrow)
                if mk and tomorrow in cur and tomorrow in prev[1]:
                    move = cur[tomorrow] - prev[1][tomorrow]
                    if abs(move) >= MIN_MOVE:
                        ts = int(public.timestamp())
                        now = [price_before(s, ts) for s in mk.series]
                        if all(x is None for x in now):
                            pass
                        else:
                            fav = max(range(len(now)), key=lambda i: now[i][1] if now[i] else -1)
                            pick = fav + (1 if move > 0 else -1)
                            if 0 <= pick < len(mk.buckets):
                                got = price_after(mk.series[pick], ts)
                                if got and P_LO <= got[1] <= P_HI:
                                    cost = buy_cost(got[1])
                                    shares = STAKE / cost
                                    won = pick == mk.winner
                                    later = price_before(mk.series[pick], ts + 3 * 3600)
                                    pnl_hold = shares * (1.0 if won else 0.0) - STAKE
                                    pnl_3h = shares * sell_value(later[1]) - STAKE if later else None
                                    trades.append(dict(
                                        day=str(tomorrow), model=model.split("_")[0], run=init.strftime("%m-%d %Hz"),
                                        public_hkt=public.astimezone(HKT).strftime("%m-%d %H:%M"),
                                        old=round(prev[1][tomorrow], 2), new=round(cur[tomorrow], 2), move=round(move, 2),
                                        favourite=mk.labels[fav], bought=mk.labels[pick], price=round(got[1], 4),
                                        price_3h=round(later[1], 4) if later else "",
                                        drift_3h=round(later[1] - got[1], 4) if later else "",
                                        winner=mk.labels[mk.winner], hko_max=actuals.get(tomorrow, ""),
                                        won=won, pnl_hold=round(pnl_hold, 2),
                                        pnl_sell_3h=round(pnl_3h, 2) if pnl_3h is not None else ""))
            prev = (init, cur) if cur else prev
            init += timedelta(hours=6)
        print(f"{model}: done, trades so far {len(trades)}", flush=True)

    today = date.today().isoformat()
    tag = f" (runs +{EXTRA_DELAY_H:g}h later)" if EXTRA_DELAY_H else ""
    out = HERE / f"backtest from {today} to {START} - Bilberry forecast-change rule HK{tag}.csv"
    if trades:
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    summarise(trades, out.with_suffix(".txt"), len(markets))


def summarise(trades, path, n_markets):
    L = [f"Bilberry forecast-change rule on Hong Kong, {START}..{END} ({n_markets} settled markets)",
         f"public delay ECMWF {MODELS['ecmwf_ifs025']}h, GFS {MODELS['gfs_seamless']}h, +{EXTRA_DELAY_H}h extra, +{REACT_MIN} min reaction", ""]
    if not trades:
        L.append("no trades")
    for name, sel in (("ALL", trades), ("ECMWF", [t for t in trades if t["model"] == "ecmwf"]),
                      ("GFS", [t for t in trades if t["model"] == "gfs"])):
        if not sel:
            continue
        n = len(sel)
        wins = sum(t["won"] for t in sel)
        hold = sum(t["pnl_hold"] for t in sel)
        s3 = [t["pnl_sell_3h"] for t in sel if t["pnl_sell_3h"] != ""]
        dr = [t["drift_3h"] for t in sel if t["drift_3h"] != ""]
        avgp = mean(t["price"] for t in sel)
        L += [f"[{name}] trades {n}, won {wins} ({100 * wins / n:.1f}%) vs average price paid {100 * avgp:.1f}c",
              f"   hold to settlement: P&L ${hold:,.2f} on ${STAKE * n:,.0f} staked (ROI {100 * hold / (STAKE * n):+.1f}%)",
              f"   sell after 3 h:     P&L ${sum(s3):,.2f} on {len(s3)} trades" if s3 else "   sell after 3 h: n/a",
              f"   price drift in the 3 h after buying: mean {100 * mean(dr):+.2f}c, median {100 * median(dr):+.2f}c, "
              f"up {sum(x > 0 for x in dr)} / down {sum(x < 0 for x in dr)}" if dr else "", ""]
    # by month, to see whether it is stable
    bym = defaultdict(list)
    for t in trades:
        bym[t["day"][:7]].append(t["pnl_hold"])
    L.append("by month (hold): " + ", ".join(f"{k} {len(v)}t ${sum(v):+.0f}" for k, v in sorted(bym.items())))
    path.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
