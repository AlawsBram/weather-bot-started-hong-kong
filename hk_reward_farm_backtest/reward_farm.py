"""HK liquidity-reward farming backtest: two-sided 20-share quotes 1c off mid in every Hong Kong
daily-max-temperature bucket market, reward income (Polymarket liquidity-rewards formula) minus
pick-off losses, split by bucket type (EVE / LIVE / DEAD / FAR), from 1-minute CLOB price history.

Inputs (all already on disk or public, no keys):
  * kill list  ..\\hk_tape_backtest\\backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket first-mover prize from the trade tape.csv
  * gamma event cache ..\\hk_tape_backtest\\cache\\ev_YYYY-MM-DD.json
  * 1-minute prices  https://clob.polymarket.com/prices-history (cached in cache\\px_<token>.json, atomic writes,
    shared with the sibling folder hk_deadbucket_only_backtest)

Pool model (headline, measured live 2026-10-23 19:19Z): $100/day per event, only 3 buckets rewarded,
$50 to the most likely bucket and $25 to each of the other two; the pool exists on day-1 and the event
day and is $0 after 23:59 HKT of the event day.  Historical allocation is unknown -> proxy = the 3 buckets
with the highest 1-minute YES price at 00:00 HKT of the event day (no look-ahead for the event day).
Alternative pool shape kept for comparison ("rank"): $200/day split 53/50/46/39/7/4/1 by rank of distance
from the live bucket (the first brief's assumption, now known to be two events added together).

Run:  python reward_farm.py [--start 2026-04-15] [--end 2026-10-20] [--days N] [--no-fetch]
"""
import argparse, csv, json, logging, os, re, statistics, sys, threading, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HKT = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"; CACHE.mkdir(exist_ok=True)
TAPE = ROOT.parent / "hk_tape_backtest"
KILLS = TAPE / "backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket first-mover prize from the trade tape.csv"
EV_CACHE = TAPE / "cache"
PX_URL = "https://clob.polymarket.com/prices-history"

# ---- model constants --------------------------------------------------------------------------
MAX_SPREAD_C = 4.5                       # rewardsMaxSpread on every HK market in the cache (confirmed live)
POOL_TOTAL = 100.0                       # $/day per event (measured 2026-10-23: HK Oct-24 highest = $100)
POOL_TOP3 = [50, 25, 25]                 # $/day to the 3 rewarded buckets, by 00:00 HKT event-day YES price rank
POOL_RANK_TOTAL = 200.0                  # old assumption kept as a sensitivity
POOL_RANK_SHAPE = [53, 50, 46, 39, 7, 4, 1]
COMPS = [20, 45, 200]                    # everyone else's Q_min, score units (today's measurement: 10-203, median ~45)
LO, HI = 0.02, 0.98                      # outside this the market is untradeable for rewards
WINDOW_BEFORE_H, WINDOW_AFTER_H = 24, 30 # fetch day-1 00:00 HKT .. day+1 06:00 HKT
NMIN = (WINDOW_BEFORE_H + WINDOW_AFTER_H) * 60
QUOTE_END_H = 24                         # quote until 23:59 HKT of the event day (pool is $0 after local midnight)
# configs: name -> (distance from mid in cents, size rule)
CONFIGS = {"base": (1, 20), "s2c": (2, 20), "s10": (1, 10), "smin": (1, "minsize")}
TYPES = ["EVE", "LIVE", "DEAD", "FAR"]

log = logging.getLogger("rf")
log.setLevel(logging.INFO)
_h1 = logging.StreamHandler(sys.stdout); _h2 = logging.FileHandler(ROOT / "run.log", encoding="utf-8")
for h in (_h1, _h2):
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")); log.addHandler(h)


def bucket_key(lbl):
    n = float(re.search(r"(-?\d+)", lbl).group(1))
    return n - 0.5 if "below" in lbl else (n + 0.5 if "higher" in lbl else n)


# ---- price fetch / cache ----------------------------------------------------------------------
_tls = threading.local()
def _sess():
    if not hasattr(_tls, "s"):
        s = requests.Session(); s.headers["User-Agent"] = "hk-reward-farm-backtest/0.1 (research)"; _tls.s = s
    return _tls.s


def load_cached(tok):
    f = CACHE / f"px_{tok}.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "history" in d:
                return d
        except Exception:
            pass
    return None


def fetch_px(tok, st, en):
    d = load_cached(tok)
    if d is not None:
        return d
    f = CACHE / f"px_{tok}.json"
    for i in range(6):
        try:
            r = _sess().get(PX_URL, params=dict(market=tok, startTs=st, endTs=en, fidelity=1), timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"http {r.status_code}")
            r.raise_for_status()
            d = r.json()
            if not isinstance(d, dict) or "history" not in d:
                raise RuntimeError("bad json")
            tmp = CACHE / f"px_{tok}.{os.getpid()}.{threading.get_ident()}.tmp"
            tmp.write_text(json.dumps(d), encoding="utf-8")
            for k in range(5):
                try:
                    os.replace(tmp, f); break
                except PermissionError:
                    time.sleep(0.2)
            else:
                tmp.unlink(missing_ok=True)
            time.sleep(0.1)
            return d
        except Exception as e:
            wait = 2 * 2 ** i
            log.warning(f"fetch {tok[:12]}.. attempt {i + 1} failed ({e}); sleeping {wait}s")
            time.sleep(wait)
    log.error(f"FETCH FAILED {tok}")
    return None


def to_grid(hist, st, n):
    """1-minute grid: last observed price inside each minute, forward-filled; None before first point."""
    g = [None] * n
    for pt in sorted(hist, key=lambda x: x["t"]):
        i = (int(pt["t"]) - st) // 60
        if 0 <= i < n:
            g[i] = float(pt["p"])
    last = None
    for i in range(n):
        if g[i] is None:
            g[i] = last
        else:
            last = g[i]
    return g


# ---- reward maths -----------------------------------------------------------------------------
def score(s_cents):
    return ((MAX_SPREAD_C - s_cents) / MAX_SPREAD_C) ** 2 if 0 <= s_cents < MAX_SPREAD_C else 0.0


def q_min(p, bid, ask, size):
    q1 = score((p - bid) * 100) * size
    q2 = score((ask - p) * 100) * size
    if 0.10 <= p <= 0.90:
        return max(min(q1, q2), max(q1, q2) / 3)
    return min(q1, q2)


# ---- accumulator -------------------------------------------------------------------------------
def new_cell():
    c = {"minutes": 0, "pool_min": 0.0, "rewarded_minutes": 0}
    for name in CONFIGS:
        for C in COMPS:
            c[f"{name}_reward_C{C}"] = 0.0
        for k in ("pickoffs", "loss_mtm", "loss_hold", "fee_if_taker"):
            c[f"{name}_{k}"] = 0.0
    for C in COMPS:
        c[f"base_rankpool_reward_C{C}"] = 0.0
    c["base_tick_loss"] = 0.0; c["base_tick_pickoffs"] = 0.0
    # trade-tape reality check (only markets with a cached tape = the 567 killed buckets)
    for kk in ("tape_minutes", "tape_reward_C45", "tape_midfills", "tape_midloss", "tape_fills", "tape_shares", "tape_loss_mtm", "tape_loss_hold"):
        c[kk] = 0.0
    return c


def load_tape(cid):
    f = TAPE / "cache" / f"tr_{cid[:18]}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


class Sim:
    def __init__(self, kills, tknow_fallback):
        self.kills = kills; self.tknow_fallback = tknow_fallback
        self.acc = defaultdict(new_cell)       # (cal_day, hour, type) -> cell
        self.checks = defaultdict(int); self.notes = []; self.rewarded = []

    def event(self, D, ev):
        ms = sorted(ev["markets"], key=lambda m: bucket_key(m["groupItemTitle"]))
        k = len(ms)
        labels = [m["groupItemTitle"] for m in ms]
        toks = [json.loads(m["clobTokenIds"])[0] for m in ms]
        yfin = [1.0 if json.loads(m["outcomePrices"])[0] == "1" else 0.0 for m in ms]
        minsz = [int(m.get("rewardsMinSize") or 20) for m in ms]
        Dm = datetime(D.year, D.month, D.day, tzinfo=HKT)
        st = int((Dm - timedelta(hours=WINDOW_BEFORE_H)).timestamp()); day0 = int(Dm.timestamp())
        quote_end = day0 + QUOTE_END_H * 3600
        hists = [load_cached(t) for t in toks]
        for j, h in enumerate(hists):
            if h is None:
                self.checks["markets_without_prices"] += 1
            elif len(h["history"]) < 100:
                self.checks["markets_with_sparse_prices(<100 pts)"] += 1
        grids = [to_grid(h["history"], st, NMIN) if h is not None else [None] * NMIN for h in hists]
        self.checks["markets"] += k
        # ---- pool (headline): 3 buckets with the highest YES price at 00:00 HKT of the event day
        m0 = WINDOW_BEFORE_H * 60
        p0 = []
        for j in range(k):
            v = grids[j][m0]
            if v is None:                                   # first available price of the event day
                v = next((grids[j][i] for i in range(m0, NMIN) if grids[j][i] is not None), None)
                self.checks["pool_pick_used_first_price_of_day"] += 1
            p0.append(v if v is not None else -1.0)
        top3 = sorted(range(k), key=lambda j: (-p0[j], -j))[:3]
        pool_top3 = [0.0] * k
        for rank, j in enumerate(top3):
            pool_top3[j] = POOL_TOP3[rank] / 1440.0
        win_j = yfin.index(1.0) if 1.0 in yfin else None
        self.rewarded.append(dict(day=str(D), rewarded=[labels[j] for j in top3], p0=[round(p0[j], 3) for j in top3],
                                  winner=labels[win_j] if win_j is not None else "", winner_rewarded=win_j in top3))
        if win_j in top3:
            self.checks["winner_among_3_rewarded"] += 1
        # ---- dead times from the kill list
        kt = {}
        for r in self.kills.get(str(D), []):
            if r["bucket"] not in labels:
                self.checks["kill_label_not_in_event"] += 1; continue
            j = labels.index(r["bucket"])
            hh, mm = map(int, r["reading"].split(":"))
            tk = int(r["t_know_s"]) if r["t_know_s"] != "" else self.tknow_fallback
            if r["t_know_s"] == "":
                self.checks["kills_using_fallback_t_know"] += 1
            kt[j] = int((Dm + timedelta(hours=hh, minutes=mm)).timestamp()) + tk
        dead = [None] * k
        lowest = min(kt) if kt else None
        for j in range(k):
            cands = [kt[i] for i in kt if i >= j]
            if cands:
                dead[j] = min(cands)
            if lowest is not None and j < lowest:
                dead[j] = day0 if dead[j] is None else min(dead[j], day0)
        if not kt:
            self.checks["days_without_any_kill"] += 1
        final_live = next((j for j in range(k) if dead[j] is None), None)
        if win_j is None or final_live != win_j:
            self.checks["final_live_bucket_!=_winner"] += 1
            self.notes.append(f"{D}: final live {labels[final_live] if final_live is not None else None} vs winner {labels[win_j] if win_j is not None else None}")

        def live_at(t):
            return next((i for i in range(k) if dead[i] is None or dead[i] > t), None)

        def typ(j, t):
            if t < day0:
                return "EVE"
            if dead[j] is not None and dead[j] <= t:
                return "DEAD"
            return "LIVE" if j == live_at(t) else "FAR"

        centre = {(j, d): None for j in range(k) for d in (1, 2)}
        prev_key = [None] * k
        for m in range(NMIN):
            t = st + m * 60; tt = datetime.fromtimestamp(t, HKT)
            ps = [g[m] for g in grids]
            if t < day0:
                cand = [(p, j) for j, p in enumerate(ps) if p is not None]
                center = max(cand)[1] if cand else k // 2
                types = ["EVE"] * k
            else:
                live = live_at(t)
                center = live if live is not None else k - 1
                types = ["DEAD" if (dead[j] is not None and dead[j] <= t) else ("LIVE" if j == live else "FAR") for j in range(k)]
            order = sorted(range(k), key=lambda j: (abs(j - center), -j))
            pool_rank = [0.0] * k
            for rank, j in enumerate(order):
                if rank < len(POOL_RANK_SHAPE):
                    pool_rank[j] = POOL_RANK_SHAPE[rank] / 1440.0
            for j in range(k):
                p = ps[j]
                key = (tt.date(), tt.hour, types[j])
                filled = {1: False, 2: False}
                # (a) pick-off check against the quote that rested during the previous minute
                if p is not None and prev_key[j] is not None:
                    for d in (1, 2):
                        c = centre[(j, d)]
                        if c is None:
                            continue
                        bid, ask = c - d / 100, c + d / 100
                        side = None
                        if p <= bid + 1e-9:
                            side, px, mtm, hold = "buy", bid, bid - p, bid - yfin[j]
                        elif p >= ask - 1e-9:
                            side, px, mtm, hold = "sell", ask, p - ask, yfin[j] - ask
                        if side:
                            filled[d] = True
                            cell = self.acc[prev_key[j]]
                            fee = 0.05 * px * (1 - px)
                            for name, (dd, sz) in CONFIGS.items():
                                if dd != d:
                                    continue
                                size = minsz[j] if sz == "minsize" else sz
                                cell[f"{name}_pickoffs"] += 1
                                cell[f"{name}_loss_mtm"] += size * mtm
                                cell[f"{name}_loss_hold"] += size * hold
                                cell[f"{name}_fee_if_taker"] += size * fee
                # (b) quote for this minute (only while the pool exists: until 23:59 HKT of the event day)
                if p is None or p < LO or p > HI or t >= quote_end:
                    for d in (1, 2):
                        centre[(j, d)] = None
                    prev_key[j] = None
                    continue
                cell = self.acc[key]
                cell["minutes"] += 1; cell["pool_min"] += pool_top3[j]
                if pool_top3[j] > 0:
                    cell["rewarded_minutes"] += 1
                for d in (1, 2):
                    c = centre[(j, d)]
                    if c is None or filled[d] or abs(p - c) >= 0.01 - 1e-9:
                        c = p; centre[(j, d)] = c
                    bid, ask = c - d / 100, c + d / 100
                    for name, (dd, sz) in CONFIGS.items():
                        if dd != d:
                            continue
                        size = minsz[j] if sz == "minsize" else sz
                        q = q_min(p, bid, ask, size)
                        for C in COMPS:
                            cell[f"{name}_reward_C{C}"] += pool_top3[j] * q / (q + C)
                            if name == "base":
                                cell[f"base_rankpool_reward_C{C}"] += pool_rank[j] * q / (q + C)
                prev_key[j] = key
        # trade-tape reality check: our bid/ask only fill when a TAKER actually traded through our price
        # (taker SELL YES / BUY NO at <= our bid fills the bid; taker BUY YES / SELL NO at >= our ask fills the ask),
        # shares = min(20, qualifying taker size in that minute); marked at the next minute's mid; same quotes as base.
        for j in range(k):
            tape = load_tape(ms[j]["conditionId"])
            if tape is None:
                continue
            self.checks["markets_with_trade_tape"] += 1
            bym = defaultdict(list)
            for tr in tape:
                ts_ = int(tr["timestamp"]); i = (ts_ - st) // 60
                if 0 <= i < NMIN:
                    yes_px = float(tr["price"]) if tr["outcome"] == "Yes" else 1 - float(tr["price"])
                    sells_yes = (tr["side"] == "SELL") == (tr["outcome"] == "Yes")   # SELL Yes or BUY No
                    bym[i].append((yes_px, sells_yes, float(tr["size"])))
            c = None; prev_cell = None
            for m in range(NMIN):
                t = st + m * 60; p = grids[j][m]
                if p is None or p < LO or p > HI or t >= quote_end:
                    c = None; prev_cell = None; continue
                tt = datetime.fromtimestamp(t, HKT)
                cell = self.acc[(tt.date(), tt.hour, typ(j, t))]
                if c is None or abs(p - c) >= 0.01 - 1e-9:
                    c = p
                bid, ask = c - 0.01, c + 0.01
                cell["tape_minutes"] += 1
                q = q_min(p, bid, ask, 20)
                cell["tape_reward_C45"] += pool_top3[j] * q / (q + 45)
                pn = grids[j][m + 1] if m + 1 < NMIN else p
                if pn is None:
                    pn = p
                # mid-move fills on this same market (for the ratio)
                if pn <= bid + 1e-9 or pn >= ask - 1e-9:
                    cell["tape_midfills"] += 1
                    cell["tape_midloss"] += 20 * ((bid - pn) if pn <= bid + 1e-9 else (pn - ask))
                # real taker trades through our price during this minute
                sb = min(20.0, sum(sz for yp, sy, sz in bym.get(m, []) if sy and yp <= bid + 1e-9))
                sa = min(20.0, sum(sz for yp, sy, sz in bym.get(m, []) if (not sy) and yp >= ask - 1e-9))
                if sb > 0:
                    cell["tape_fills"] += 1; cell["tape_shares"] += sb
                    cell["tape_loss_mtm"] += sb * (bid - pn); cell["tape_loss_hold"] += sb * (bid - yfin[j])
                if sa > 0:
                    cell["tape_fills"] += 1; cell["tape_shares"] += sa
                    cell["tape_loss_mtm"] += sa * (pn - ask); cell["tape_loss_hold"] += sa * (yfin[j] - ask)
                if sb > 0 or sa > 0:
                    c = None            # re-quote from the next minute's mid
        # tick-level pick-off sensitivity (base config, d=1, 20 sh): every observed price point is a price update
        for j in range(k):
            h = hists[j]
            if h is None:
                continue
            c = None; quoting = False; qkey = None
            for pt in sorted(h["history"], key=lambda x: x["t"]):
                t, p = int(pt["t"]), float(pt["p"])
                if t < st or t >= st + NMIN * 60:
                    continue
                if quoting and c is not None:
                    bid, ask = c - 0.01, c + 0.01
                    if p <= bid + 1e-9 or p >= ask - 1e-9:
                        loss = (bid - p) if p <= bid + 1e-9 else (p - ask)
                        cell = self.acc[qkey]; cell["base_tick_loss"] += 20 * loss; cell["base_tick_pickoffs"] += 1
                        c = None
                if p < LO or p > HI or t >= quote_end:
                    quoting = False; c = None; continue
                tt = datetime.fromtimestamp(t, HKT)
                qkey = (tt.date(), tt.hour, typ(j, t)); quoting = True
                if c is None or abs(p - c) >= 0.01 - 1e-9:
                    c = p


# ---- aggregation / reporting ------------------------------------------------------------------
def add(dst, src):
    for kk, v in src.items():
        dst[kk] = dst.get(kk, 0) + v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-15"); ap.add_argument("--end", default="2026-10-20")
    ap.add_argument("--days", type=int, default=0, help="only the first N days (smoke test)")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    t_run = time.time()
    kills_rows = list(csv.DictReader(open(KILLS, encoding="utf-8")))
    kills = defaultdict(list)
    for r in kills_rows:
        kills[r["day"]].append(r)
    tks = [int(r["t_know_s"]) for r in kills_rows if r["t_know_s"] != ""]
    tknow_fb = int(statistics.median(tks))
    log.info(f"kill list: {len(kills_rows)} kills on {len(kills)} days; t_know median {tknow_fb}s used as fallback for {len(kills_rows) - len(tks)} blanks")
    days = []
    d = date.fromisoformat(a.start)
    while d <= date.fromisoformat(a.end):
        f = EV_CACHE / f"ev_{d}.json"
        if f.exists():
            ev = json.load(open(f, encoding="utf-8"))
            if ev:
                days.append((d, ev[0]))
        d += timedelta(days=1)
    if a.days:
        days = days[:a.days]
    log.info(f"events: {len(days)} ({days[0][0]} .. {days[-1][0]})")
    # fetch
    jobs = []
    for D, ev in days:
        Dm = datetime(D.year, D.month, D.day, tzinfo=HKT)
        st = int((Dm - timedelta(hours=WINDOW_BEFORE_H)).timestamp()); en = int((Dm + timedelta(hours=WINDOW_AFTER_H)).timestamp())
        for m in ev["markets"]:
            jobs.append((json.loads(m["clobTokenIds"])[0], st, en))
    have = sum(1 for t, _, _ in jobs if (CACHE / f"px_{t}.json").exists())
    log.info(f"price series needed {len(jobs)}, already cached {have}")
    if not a.no_fetch:
        done = [0]
        def work(j):
            fetch_px(*j); done[0] += 1
            if done[0] % 100 == 0:
                log.info(f"fetched {done[0]}/{len(jobs)}")
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(work, jobs))
        log.info(f"fetch done in {time.time() - t_run:.0f}s")
    # simulate
    sim = Sim(kills, tknow_fb)
    for i, (D, ev) in enumerate(days):
        sim.event(D, ev)
        if i % 20 == 0:
            log.info(f"simulated {i + 1}/{len(days)} ({D})")
    log.info(f"simulation done in {time.time() - t_run:.0f}s; checks {dict(sim.checks)}")
    for n in sim.notes[:20]:
        log.info("  check: " + n)

    # complete calendar days = both legs (day-of and eve-of-next) present
    ev_days = {D for D, _ in days}
    complete = sorted(D for D in ev_days if (D + timedelta(days=1)) in ev_days)
    all_days = sorted({k[0] for k in sim.acc})
    log.info(f"calendar days with any activity {len(all_days)}, complete days {len(complete)} ({complete[0]} .. {complete[-1]})")

    today = datetime.now().strftime("%Y-%m-%d")
    base = f"backtest from {today} to {a.start} - HK liquidity reward farming net of pick-offs"
    by_day_type = defaultdict(dict); by_day = defaultdict(dict); by_type = defaultdict(dict)
    by_hour = defaultdict(dict); by_hour_type = defaultdict(dict)
    for (D, h, ty), cell in sim.acc.items():
        add(by_day_type[(D, ty)], cell)
        if D in complete:
            add(by_day[D], cell); add(by_type[ty], cell); add(by_hour[h], cell); add(by_hour_type[(h, ty)], cell)
    cols = ["minutes_quoted", "rewarded_minutes", "max_pool_$"]
    for name in CONFIGS:
        for C in COMPS:
            cols += [f"{name}_reward_C{C}"]
        cols += [f"{name}_pickoffs", f"{name}_loss_mtm", f"{name}_loss_hold", f"{name}_fee_if_taker"]
        for C in COMPS:
            cols += [f"{name}_net_mtm_C{C}"]
    cols += ["base_tick_pickoffs", "base_tick_loss", "base_net_hold_C45", "base_reward_C45_per_$100_pool"]
    cols += [f"base_rankpool200_reward_C{C}" for C in COMPS]
    TAPE_COLS = ["tape_minutes", "tape_reward_C45", "tape_midfills", "tape_midloss", "tape_fills", "tape_shares", "tape_loss_mtm", "tape_loss_hold"]
    cols += TAPE_COLS + ["tape_net_mtm_C45"]

    def row(cell):
        r = {"minutes_quoted": cell.get("minutes", 0), "rewarded_minutes": cell.get("rewarded_minutes", 0), "max_pool_$": round(cell.get("pool_min", 0), 3)}
        for name in CONFIGS:
            for C in COMPS:
                r[f"{name}_reward_C{C}"] = round(cell.get(f"{name}_reward_C{C}", 0), 3)
                r[f"{name}_net_mtm_C{C}"] = round(cell.get(f"{name}_reward_C{C}", 0) - cell.get(f"{name}_loss_mtm", 0), 3)
            for kk in ("pickoffs", "loss_mtm", "loss_hold", "fee_if_taker"):
                r[f"{name}_{kk}"] = round(cell.get(f"{name}_{kk}", 0), 3)
        r["base_tick_pickoffs"] = cell.get("base_tick_pickoffs", 0); r["base_tick_loss"] = round(cell.get("base_tick_loss", 0), 3)
        r["base_net_hold_C45"] = round(cell.get("base_reward_C45", 0) - cell.get("base_loss_hold", 0), 3)
        r["base_reward_C45_per_$100_pool"] = round(cell.get("base_reward_C45", 0) * 100 / POOL_TOTAL, 3)
        for C in COMPS:
            r[f"base_rankpool200_reward_C{C}"] = round(cell.get(f"base_rankpool_reward_C{C}", 0), 3)
        for kk in TAPE_COLS:
            r[kk] = round(cell.get(kk, 0), 3)
        r["tape_net_mtm_C45"] = round(cell.get("tape_reward_C45", 0) - cell.get("tape_loss_mtm", 0), 3)
        return r

    with open(ROOT / (base + ".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["day", "complete_day", "bucket_type"] + cols); w.writeheader()
        for (D, ty) in sorted(by_day_type, key=lambda x: (x[0], TYPES.index(x[1]))):
            w.writerow({"day": str(D), "complete_day": D in complete, "bucket_type": ty, **row(by_day_type[(D, ty)])})
    with open(ROOT / (base + " - by hour of day HKT.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["hour_hkt", "bucket_type"] + cols); w.writeheader()
        for h in range(24):
            w.writerow({"hour_hkt": h, "bucket_type": "ALL", **row(by_hour.get(h, {}))})
            for ty in TYPES:
                if (h, ty) in by_hour_type:
                    w.writerow({"hour_hkt": h, "bucket_type": ty, **row(by_hour_type[(h, ty)])})
    with open(ROOT / (base + " - rewarded buckets per day (00h HKT price proxy).csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["day", "rewarded", "p0", "winner", "winner_rewarded"]); w.writeheader()
        for r in sim.rewarded:
            w.writerow({**r, "rewarded": " | ".join(r["rewarded"]), "p0": " | ".join(map(str, r["p0"]))})

    # ---- summary text
    L = [f"# {base}", f"events {len(days)} ({days[0][0]}..{days[-1][0]}), complete calendar days {len(complete)} ({complete[0]}..{complete[-1]}); all $ below are over complete days",
         f"model: 2-sided quotes in every bucket from day-1 00:00 HKT until 23:59 HKT of the event day; reward = pool/1440 * Qmin/(Qmin+C) per quoted minute;",
         f"       pool ${POOL_TOTAL:.0f}/event/day on the 3 buckets with the highest YES price at 00:00 HKT of the event day, split {POOL_TOP3} (ASSUMPTION, see md);",
         f"       untradeable if mid<{LO} or >{HI}; pick-off = next 1-min mid crosses our order, marked at the new mid (mtm) or held to resolution (hold)",
         f"data checks: {dict(sim.checks)}", ""]
    tot = {}
    for D in complete:
        add(tot, by_day[D])
    nd = len(complete)

    def daily(name, C, kind="mtm"):
        return [by_day[D].get(f"{name}_reward_C{C}", 0) - by_day[D].get(f"{name}_loss_{kind}", 0) for D in complete]

    def line(name, C, kind="mtm", label=None, rewkey=None):
        rk = rewkey or f"{name}_reward_C{C}"
        nets = [by_day[D].get(rk, 0) - by_day[D].get(f"{name}_loss_{kind}", 0) for D in complete]
        rew = tot.get(rk, 0); loss = tot.get(f"{name}_loss_{kind}", 0)
        pos = sum(1 for x in nets if x > 0)
        worst = min(zip(nets, complete)) if nets else (0, None)
        return (f"{label or name:34s} C={C:>3}: reward ${rew:8.2f}  pick-off loss ${loss:8.2f}  NET ${rew - loss:8.2f}  "
                f"= ${(rew - loss) / nd:6.2f}/day  median ${statistics.median(nets):6.2f}/day  days net>0 {pos}/{nd} ({100 * pos / nd:.0f}%)  "
                f"worst day ${worst[0]:.2f} ({worst[1]})  pick-offs {tot.get(f'{name}_pickoffs', 0):.0f}")

    L.append("## HEADLINE (base: 20 shares each side, 1c off mid, mark-to-new-mid, $100 pool on 3 buckets)")
    for C in COMPS:
        L.append(line("base", C))
    L.append(f"reward per $100 of event pool/day (C=45): ${tot.get('base_reward_C45', 0) * 100 / POOL_TOTAL / nd:.2f}/day; quoted minutes {tot.get('minutes', 0):,} of which in a rewarded bucket {tot.get('rewarded_minutes', 0):,}; max pool reachable ${tot.get('pool_min', 0):.2f}")
    L.append(f"fee column (0.05*p*(1-p) per filled share, charged to TAKERS, so our cost is $0): would be ${tot.get('base_fee_if_taker', 0):.2f} in total")
    L.append("")
    L.append("## Held-to-resolution instead of marking at the new mid (base config)")
    for C in COMPS:
        L.append(line("base", C, "hold", "base, hold to resolution"))
    L.append("")
    L.append("## Sensitivities (mark-to-mid)")
    for name, lab in (("s2c", "2c off mid, 20 sh"), ("s10", "1c off mid, 10 sh"), ("smin", "1c, size = market rewardsMinSize")):
        for C in COMPS:
            L.append(line(name, C, "mtm", lab))
    for C in COMPS:
        L.append(line("base", C, "mtm", "OLD pool shape $200 rank 53/50/46..", rewkey=f"base_rankpool_reward_C{C}"))
    L.append(f"tick-level pick-offs (every observed price point, base quotes): {tot.get('base_tick_pickoffs', 0):.0f} fills, loss ${tot.get('base_tick_loss', 0):.2f} (vs 1-min grid ${tot.get('base_loss_mtm', 0):.2f})")
    L.append("")
    L.append("## Trade-tape reality check (only the killed buckets that have a cached tape; fills need a real taker trade through our price)")
    L.append(f"{'type':6s} {'minutes':>9s} {'rew C45':>9s} | {'mid-move fills':>14s} {'mid loss':>9s} | {'tape fills':>10s} {'shares':>8s} {'loss@mid':>9s} {'loss@mid-1c':>11s} {'loss@hold':>9s} | {'NET (tape@mid)':>14s} {'NET (tape@mid-1c)':>17s} {'NET (tape@hold)':>15s} {'NET (mid-move)':>14s}")
    ttot = {}
    for ty in TYPES + ["ALL"]:
        c = ttot if ty == "ALL" else by_type.get(ty, {})
        if ty != "ALL":
            add(ttot, {kk: c.get(kk, 0) for kk in TAPE_COLS})
        rew = c.get('tape_reward_C45', 0); lm = c.get('tape_loss_mtm', 0); lx = lm + 0.01 * c.get('tape_shares', 0); lh = c.get('tape_loss_hold', 0)
        L.append(f"{ty:6s} {c.get('tape_minutes', 0):9,.0f} {rew:9.2f} | {c.get('tape_midfills', 0):14,.0f} {c.get('tape_midloss', 0):9.2f} | "
                 f"{c.get('tape_fills', 0):10,.0f} {c.get('tape_shares', 0):8,.0f} {lm:9.2f} {lx:11.2f} {lh:9.2f} | "
                 f"{rew - lm:14.2f} {rew - lx:17.2f} {rew - lh:15.2f} {rew - c.get('tape_midloss', 0):14.2f}")
    L.append("loss@mid = filled shares marked at the next minute's mid (optimistic: nobody exits at mid); loss@mid-1c = same but paying 1c/share to get out; loss@hold = kept to resolution")
    for kind, fn in (("tape@mid", lambda c: c.get("tape_loss_mtm", 0)), ("tape@mid-1c", lambda c: c.get("tape_loss_mtm", 0) + 0.01 * c.get("tape_shares", 0)), ("tape@hold", lambda c: c.get("tape_loss_hold", 0))):
        tape_nets = [by_day[D].get("tape_reward_C45", 0) - fn(by_day[D]) for D in complete if by_day[D].get("tape_minutes", 0) > 0]
        if tape_nets:
            L.append(f"   {kind:12s} NET per day on those markets: mean ${sum(tape_nets) / len(tape_nets):.2f}, median ${statistics.median(tape_nets):.2f}, days net>0 {sum(1 for x in tape_nets if x > 0)}/{len(tape_nets)} ({100 * sum(1 for x in tape_nets if x > 0) / len(tape_nets):.0f}%)")
    L.append("")
    L.append("## By bucket type (complete days, base config)")
    L.append(f"{'type':6s} {'minutes':>9s} {'rewarded':>9s} {'maxpool$':>9s} | {'rew C20':>9s} {'rew C45':>9s} {'rew C200':>9s} | {'pickoffs':>8s} {'loss mtm':>9s} {'loss hold':>9s} | {'NET C20':>9s} {'NET C45':>9s} {'NET C200':>9s} | days net>0 (C45)")
    for ty in TYPES:
        c = by_type.get(ty, {})
        nets = [by_day_type.get((D, ty), {}).get("base_reward_C45", 0) - by_day_type.get((D, ty), {}).get("base_loss_mtm", 0) for D in complete]
        pos = sum(1 for x in nets if x > 0); act = sum(1 for D in complete if (D, ty) in by_day_type)
        L.append(f"{ty:6s} {c.get('minutes', 0):9,} {c.get('rewarded_minutes', 0):9,} {c.get('pool_min', 0):9.2f} | " + " ".join(f"{c.get(f'base_reward_C{C}', 0):9.2f}" for C in COMPS)
                 + f" | {c.get('base_pickoffs', 0):8.0f} {c.get('base_loss_mtm', 0):9.2f} {c.get('base_loss_hold', 0):9.2f} | "
                 + " ".join(f"{c.get(f'base_reward_C{C}', 0) - c.get('base_loss_mtm', 0):9.2f}" for C in COMPS) + f" | {pos}/{act} ({100 * pos / max(act, 1):.0f}%)")
    L.append("maxpool$ = sum over quoted minutes of that bucket's assumed pool/1440 = what a 100% share would earn")
    L.append("")
    L.append("## By hour of day (HKT, complete days, base config, C=45)")
    L.append(f"{'hour':>4s} {'minutes':>8s} {'reward':>8s} {'pickoffs':>8s} {'loss':>8s} {'NET':>8s} | per type NET (EVE/LIVE/DEAD/FAR)")
    for h in range(24):
        c = by_hour.get(h, {})
        per = " ".join(f"{by_hour_type.get((h, ty), {}).get('base_reward_C45', 0) - by_hour_type.get((h, ty), {}).get('base_loss_mtm', 0):7.2f}" for ty in TYPES)
        L.append(f"{h:4d} {c.get('minutes', 0):8,} {c.get('base_reward_C45', 0):8.2f} {c.get('base_pickoffs', 0):8.0f} {c.get('base_loss_mtm', 0):8.2f} {c.get('base_reward_C45', 0) - c.get('base_loss_mtm', 0):8.2f} | {per}")
    L.append("")
    nets45 = daily("base", 45); pos = sum(1 for x in nets45 if x > 0)
    gate = 100 * pos / nd
    L.append(f"## GATE: net > 0 on at least 70% of days under C=45 -> {pos}/{nd} = {gate:.0f}% -> {'PASS' if gate >= 70 else 'FAIL'}")
    for ty in TYPES:
        nets = [by_day_type.get((D, ty), {}).get("base_reward_C45", 0) - by_day_type.get((D, ty), {}).get("base_loss_mtm", 0) for D in complete]
        p_ = sum(1 for x in nets if x > 0)
        L.append(f"   {ty}-only quoting: {p_}/{nd} days net>0 ({100 * p_ / nd:.0f}%) -> {'PASS' if 100 * p_ / nd >= 70 else 'FAIL'}; total NET ${sum(nets):.2f}")
    L.append("")
    L.append("## Worst 5 days (base, C=45, mtm)")
    for x, D in sorted(zip(nets45, complete))[:5]:
        c = by_day[D]
        L.append(f"   {D}: net ${x:.2f}  reward ${c.get('base_reward_C45', 0):.2f} loss ${c.get('base_loss_mtm', 0):.2f} pick-offs {c.get('base_pickoffs', 0):.0f}")
    txt = "\n".join(L)
    (ROOT / (base + ".txt")).write_text(txt, encoding="utf-8")
    print(txt)
    log.info(f"wrote outputs in {time.time() - t_run:.0f}s")


if __name__ == "__main__":
    main()
