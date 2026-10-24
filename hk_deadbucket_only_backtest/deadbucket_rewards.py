"""HK dead-bucket-ONLY liquidity-reward farming backtest.

A bucket is DEAD once the day's running max has passed it (YES = 0, NO = 1 with certainty).
We quote BOTH sides only in dead buckets, only after the market "knows" (kill time = HKO stamp + t_know_s),
until 23:59 HKT of the event day (the pool is $0 after local midnight, measured live 2026-10-23).
Rewards follow docs.polymarket.com/programs/liquidity-rewards:
  S = ((v - s)/v)^2, v = 4.5c (rewardsMaxSpread of every HK market), s = distance to the midpoint in cents
  midpoint < 0.10  ->  Q_min = min(Q_one, Q_two)  (BOTH sides required, no 1/3 credit)
  our share of a 1-minute sample = Q_min / (Q_min + others), reward = pool_per_day / 1440 per sampled minute.
POOL (measured live 2026-10-23 19:19Z): $100/day per HK event, only 3 central buckets carry a line: the top one $50,
the other two $25 each, every other bucket $0. No-lookahead proxy for "central": the bucket's rank by YES midpoint at
00:00 HKT of the event day among the event's 11 buckets -> rank 1 = $50, rank 2-3 = $25, else $0.
Two legs, honest version (size 20 shares = min size, confirmed live; fee 0 on HK):
  leg A = resting BUY YES at mid - 1c  : if filled we hold worthless YES -> loss = size * price
  leg B = resting BUY NO at 1 - (mid + 1c) (the CLOB shows it as a YES ask at mid + 1c): if filled we hold NO
          worth $1 -> gain = size * (mid + 1c) at resolution, capital size * (1 - mid - 1c) tied up till then.
Fill proxy: the next 1-minute midpoint moves to or through our price.
Quotable only if our bid is strictly below the midpoint (bid 0.001 against a 0.000/0.001 book would cross) and the
midpoint is below 0.10 (otherwise the market does not price the bucket dead and we stand aside).
"""
import csv, json, os, sys, time, statistics
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

HKT = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
KILLS = ROOT.parent / "hk_tape_backtest" / "backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket first-mover prize from the trade tape.csv"
EVCACHE = ROOT.parent / "hk_tape_backtest" / "cache"
PXCACHE = ROOT.parent / "hk_reward_farm_backtest" / "cache"      # SHARED 1-min price cache with the full reward-farm backtest
MIDCACHE = ROOT / "cache"                                         # our own small cache: 00:00 HKT midpoints for ranking
PXCACHE.mkdir(parents=True, exist_ok=True); MIDCACHE.mkdir(exist_ok=True)
LOG = open(ROOT / "run.log", "a", encoding="utf-8")
V = 4.5                      # max spread, cents
SIZE = 20                    # shares per order (min size 20 confirmed live 2026-10-23)
COMP = (10, 40, 100)         # others' Q_min units in the dead bucket (today: 10-40)
POOL_BY_RANK = {1: 50.0, 2: 25.0, 3: 25.0}   # $/day; all other ranks 0
REQ_MID = 0.01               # re-quote when mid moves >= 1c
FALLBACK_TKNOW = None        # median t_know_s, filled from the kill list
TEST_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WINDOW_DAYS = (datetime(2026, 10, 20) - datetime(2026, 4, 15)).days + 1


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


ses = requests.Session()
ses.headers["User-Agent"] = "hk-deadbucket-only-backtest/0.2 (research)"


def fetch(token, t0, t1):
    d = None
    for i in range(5):
        try:
            r = ses.get("https://clob.polymarket.com/prices-history",
                        params=dict(market=token, startTs=int(t0), endTs=int(t1), fidelity=1), timeout=40)
            if r.status_code == 429:
                raise RuntimeError("429")
            r.raise_for_status()
            d = r.json()
            break
        except Exception as e:
            if i == 4:
                log("FETCH FAIL", token[:12], e)
                return None
            time.sleep(1.5 * 2 ** i)
    time.sleep(0.2)
    return d


def write_atomic(f, d):
    tmp = f.with_name(f.name + f".tmp{os.getpid()}{time.time_ns()}")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, f)


def px_history(token, t0, t1):
    """Full 1-min series day-1 00:00 HKT .. day+1 06:00 HKT, shared cache."""
    f = PXCACHE / f"px_{token}.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            h = d["history"] if isinstance(d, dict) else d
            if h:
                return h
        except Exception as e:
            log("cache unreadable, refetch", f.name, e)
    d = fetch(token, t0, t1)
    if d is None:
        return []
    write_atomic(f, d)
    return d.get("history", [])


def mid_at(token, ts):
    """YES midpoint at (or last before) ts, from the shared cache if present, else a narrow fetch cached locally."""
    f = PXCACHE / f"px_{token}.json"
    h = None
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            h = d["history"] if isinstance(d, dict) else d
        except Exception:
            h = None
    if not h:
        g = MIDCACHE / f"mid_{token}.json"
        if g.exists():
            h = json.loads(g.read_text(encoding="utf-8")).get("history", [])
        else:
            d = fetch(token, ts - 6 * 3600, ts + 3600)
            if d is None:
                return None
            write_atomic(g, d)
            h = d.get("history", [])
    before = [p for p in h if p["t"] <= ts]
    if before:
        return float(before[-1]["p"])
    after = [p for p in h if p["t"] > ts]
    return float(after[0]["p"]) if after else None


def event_markets(day):
    f = EVCACHE / f"ev_{day}.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    return {m["groupItemTitle"]: m for m in (d[0]["markets"] if d else [])}


_rank_cache = {}


def ranks_at_midnight(day):
    """{bucket label: (rank, mid)} by YES midpoint at 00:00 HKT of the event day. Cached per day on disk."""
    if day in _rank_cache:
        return _rank_cache[day]
    f = MIDCACHE / f"rank_{day}.json"
    if f.exists():
        r = json.loads(f.read_text(encoding="utf-8"))
        _rank_cache[day] = {k: tuple(v) for k, v in r.items()}
        return _rank_cache[day]
    ts = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=HKT).timestamp()
    mids = {}
    for label, m in event_markets(day).items():
        tok = json.loads(m["clobTokenIds"])[0]
        mids[label] = mid_at(tok, ts)
    order = sorted(mids, key=lambda k: -(mids[k] if mids[k] is not None else -1))
    r = {k: (i + 1, mids[k]) for i, k in enumerate(order)}
    write_atomic(f, r)
    _rank_cache[day] = r
    return r


def rt(x, tick):
    return round(round(x / tick) * tick, 6)


def score(mid, px):
    s = abs(mid - px) * 100.0
    return ((V - s) / V) ** 2 if s < V else 0.0


def simulate(k):
    day, label = k["day"], k["bucket"]
    mk = event_markets(day).get(label)
    row = dict(day=day, bucket=label, reading=k["reading"], official_max=k["official_max"], flags="")
    if not mk:
        row["flags"] = "no market in gamma cache"
        return row
    yes_tok, no_tok = json.loads(mk["clobTokenIds"])
    op = json.loads(mk["outcomePrices"]) if mk.get("outcomePrices") else []
    tick = float(mk.get("orderPriceMinTickSize") or 0.01)
    row.update(size=SIZE, gamma_min_size=mk.get("rewardsMinSize"), tick=tick, yes_final=op[0] if op else "")
    if not op or float(op[0]) != 0.0:
        row["flags"] = f"YES did not resolve 0 (outcomePrices={op})"
        return row
    rk = ranks_at_midnight(day).get(label, (None, None))
    row.update(rank_00h=rk[0], mid_00h=rk[1], pool_usd=POOL_BY_RANK.get(rk[0], 0.0))
    stamp = datetime.strptime(f"{day} {k['reading']}", "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
    tknow = int(k["t_know_s"]) if k["t_know_s"] != "" else FALLBACK_TKNOW
    row["t_know_src"] = "tape" if k["t_know_s"] != "" else f"fallback median {FALLBACK_TKNOW}s"
    kill = stamp.timestamp() + tknow
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=HKT)
    t0, t1 = (d0 - timedelta(days=1)).timestamp(), (d0 + timedelta(days=1, hours=6)).timestamp()
    midnight = (d0 + timedelta(days=1)).timestamp()
    end = midnight - 60
    if mk.get("closedTime"):
        ct = datetime.strptime(mk["closedTime"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        end = min(end, ct)
    h = px_history(yes_tok, t0, t1)
    row.update(kill_hkt=datetime.fromtimestamp(kill, HKT).strftime("%H:%M"),
               end_hkt=datetime.fromtimestamp(end, HKT).strftime("%H:%M"))
    if not h:
        row["flags"] = "no price history"
        return row
    pts = [(p["t"], float(p["p"])) for p in h]
    last = None
    pre = [p for p in pts if p[0] <= kill]
    if pre:
        last = pre[-1][1]
    pts_after = [p for p in pts if p[0] > kill]
    if not pts_after:
        row["flags"] = "no price points after kill"
        return row
    grid = []
    j = 0
    t = kill
    while t <= end:
        while j < len(pts_after) and pts_after[j][0] <= t:
            last = pts_after[j][1]
            j += 1
        if last is not None:
            grid.append((t, last))
        t += 60
    if len(grid) < 2:
        row["flags"] = "fewer than 2 minutes after kill"
        return row
    mids = [m for _, m in grid]
    n = len(grid)
    quot = unq = hi = 0
    qmid = bid = ask = None
    bid_fills = ask_fills = 0
    bid_cost = ask_gain = cap = capital = 0.0
    sh_sum = {c: 0.0 for c in COMP}
    for i in range(n):
        t, m = grid[i]
        if m >= 0.10:              # market does not price it dead -> stand aside
            hi += 1
            bid = None
            continue
        if qmid is None or abs(m - qmid) >= REQ_MID or bid is None:
            qmid = m
            bid = max(rt(m - 0.01, tick), tick)
            ask = rt(m + 0.01, tick)
            if ask - m > 0.045:
                ask = rt(m + 0.045, tick)
        if not (bid < m):          # bid on/above the midpoint = crosses the ask -> cannot rest
            unq += 1
            bid = None
            continue
        quot += 1
        qmin = min(score(m, bid), score(m, ask)) * SIZE
        for c in COMP:
            sh_sum[c] += qmin / (qmin + c)
        if i + 1 < n:
            nm = grid[i + 1][1]
            if nm <= bid:
                bid_fills += 1
                bid_cost += SIZE * bid
                bid = None
            if nm >= ask:
                ask_fills += 1
                ask_gain += SIZE * ask
                capital += SIZE * (1 - ask)
                cap = max(cap, capital)
                bid = None
    row.update(dead_min=n, quotable_min=quot, unquotable_min=unq, minutes_mid_ge_0_10=hi,
               min_mid=min(mids), med_mid=statistics.median(mids), max_mid=max(mids),
               bid_fills=bid_fills, bid_fill_loss=round(bid_cost, 3), no_fills=ask_fills,
               no_fill_gain=round(ask_gain, 3), capital_tied=round(cap, 2))
    for c in COMP:
        row[f"reward_per_$1pool_c{c}"] = round(sh_sum[c] / 1440.0, 5)
        row[f"reward_c{c}"] = round(row["pool_usd"] * sh_sum[c] / 1440.0, 4)
    return row


def main():
    global FALLBACK_TKNOW
    kills = list(csv.DictReader(open(KILLS, encoding="utf-8")))
    FALLBACK_TKNOW = int(statistics.median([int(k["t_know_s"]) for k in kills if k["t_know_s"] != ""]))
    days = sorted({k["day"] for k in kills})
    if TEST_DAYS:
        days = days[-TEST_DAYS:]
        kills = [k for k in kills if k["day"] in days]
    log(f"=== run {datetime.now():%Y-%m-%d %H:%M:%S} kills={len(kills)} days={len(days)} fallback t_know={FALLBACK_TKNOW}s size={SIZE} pool_by_rank={POOL_BY_RANK}")
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(ranks_at_midnight, days))          # ranking first (needs all 11 buckets per day)
        rows = list(ex.map(simulate, kills))
    good = [r for r in rows if "dead_min" in r]
    bad = [r for r in rows if "dead_min" not in r]
    log(f"simulated {len(good)} kills, skipped {len(bad)}: " + "; ".join(f"{r['day']} {r['bucket']}: {r['flags']}" for r in bad[:30]))
    today = datetime.now().strftime("%Y-%m-%d")
    name = f"backtest from {today} to 2026-04-15 - HK dead-bucket-only liquidity reward farming"
    cols = list(good[0].keys()) if good else list(rows[0].keys())
    for r in rows:
        for c in cols:
            r.setdefault(c, "")
    with open(ROOT / (name + ".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    # ---------- per day ----------
    D = defaultdict(lambda: defaultdict(float))
    for r in good:
        d = D[r["day"]]
        d["kills"] += 1
        d["kills_with_pool"] += 1 if r["pool_usd"] > 0 else 0
        d["pool_usd"] += r["pool_usd"]
        for key in ("dead_min", "quotable_min", "unquotable_min", "minutes_mid_ge_0_10", "bid_fills",
                    "bid_fill_loss", "no_fills", "no_fill_gain", "capital_tied"):
            d[key] += r[key]
        for c in COMP:
            d[f"reward_c{c}"] += r[f"reward_c{c}"]
            d[f"reward_per$10_c{c}"] += 10 * r[f"reward_per_$1pool_c{c}"]
            d[f"reward_gammaminsize_c{c}"] += r[f"reward_c{c}"] if (r["gamma_min_size"] or 0) <= SIZE else 0.0
    dayrows = []
    for day in sorted(D):
        d = dict(D[day])
        d["day"] = day
        for c in COMP:
            d[f"net_c{c}"] = d[f"reward_c{c}"] - d["bid_fill_loss"] + d["no_fill_gain"]
        dayrows.append(d)
    dcols = ["day"] + [k for k in dayrows[0] if k != "day"]
    with open(ROOT / (name + " - per day.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=dcols)
        w.writeheader()
        for d in dayrows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()})
    # ---------- summary ----------
    nd = len(dayrows)

    def S(key):
        return sum(d[key] for d in dayrows)

    def med(key):
        return statistics.median(d[key] for d in dayrows)

    rk = Counter(r["rank_00h"] for r in good)
    pooled = [r for r in good if r["pool_usd"] > 0]
    gms = Counter(r["gamma_min_size"] for r in good)
    L = [f"# {name}", "",
         f"kills simulated: {len(good)} of {len(rows)} (skipped {len(bad)}), calendar days with a kill: {nd} "
         f"(window has {WINDOW_DAYS} days; days without a kill earn nothing in a dead-bucket-only farm)",
         f"order size {SIZE} shares (min size confirmed live 2026-10-23); gamma's historical rewardsMinSize per kill: "
         + ", ".join(f"{s}: {c} kills" for s, c in sorted(gms.items(), key=lambda x: x[0] or 0)),
         "tick: " + ", ".join(str(t) for t in sorted({r["tick"] for r in good}))
         + f";  t_know fallback (median {FALLBACK_TKNOW}s) used on {sum(1 for r in good if 'fallback' in r['t_know_src'])} kills",
         "",
         "POOL (rank of the killed bucket by YES mid at 00:00 HKT; rank 1 = $50/day, rank 2-3 = $25/day, else $0):",
         "  killed buckets by 00:00 rank: " + ", ".join(f"rank {k}: {v}" for k, v in sorted(rk.items(), key=lambda x: (x[0] is None, x[0]))),
         f"  kills WITH a pool: {len(pooled)} of {len(good)} ({100 * len(pooled) / len(good):.0f}%); "
         f"days with >= 1 pooled kill: {sum(1 for d in dayrows if d['kills_with_pool'] > 0)} of {nd}; "
         f"total pool attached to dead buckets ${S('pool_usd'):,.0f} = ${S('pool_usd') / nd:.2f}/kill-day (of $100/day per event)",
         f"  00:00 mid of killed buckets: median {statistics.median(r['mid_00h'] for r in good if r['mid_00h'] is not None):.3f}; "
         f"pooled kills' median 00:00 mid {statistics.median(r['mid_00h'] for r in pooled):.3f}" if pooled else "  no pooled kills",
         "",
         "DEAD-BUCKET MINUTES (kill -> 23:59 HKT same day):",
         f"  total {S('dead_min'):,.0f} min = {S('dead_min') / nd:,.0f}/day mean, {med('dead_min'):,.0f}/day median; "
         f"pooled kills only: {sum(r['dead_min'] for r in pooled):,.0f} min = {sum(r['dead_min'] for r in pooled) / nd:,.0f}/day",
         f"  quotable (mid < 0.10 and our 1c-below bid rests without crossing): {S('quotable_min'):,.0f} min = {100 * S('quotable_min') / max(1, S('dead_min')):.1f}%;"
         f"  unquotable, book already 0/0.001: {S('unquotable_min'):,.0f} min = {100 * S('unquotable_min') / max(1, S('dead_min')):.1f}%;"
         f"  market still >= 0.10: {S('minutes_mid_ge_0_10'):,.0f} min = {100 * S('minutes_mid_ge_0_10') / max(1, S('dead_min')):.1f}%",
         f"  pooled kills: quotable {sum(r['quotable_min'] for r in pooled):,.0f} of {sum(r['dead_min'] for r in pooled):,.0f} min "
         f"({100 * sum(r['quotable_min'] for r in pooled) / max(1, sum(r['dead_min'] for r in pooled)):.1f}%)",
         f"  midpoint after the kill: median of per-kill medians {statistics.median(r['med_mid'] for r in good):.4f};"
         f" kills whose post-kill mid was ever >= 0.10: {sum(1 for r in good if r['max_mid'] >= 0.10)}",
         "",
         "FILLS (proxy: next 1-min midpoint reaches our price; all kills quoted, pooled or not):",
         f"  YES-bid fills (we buy worthless YES): {S('bid_fills'):.0f} fills, total loss ${S('bid_fill_loss'):.2f} = ${S('bid_fill_loss') / nd:.3f}/day;"
         f"  kills with >= 1 bid fill: {sum(1 for r in good if r['bid_fills'])} of {len(good)}; median fill price "
         f"${statistics.median([r['bid_fill_loss'] / r['bid_fills'] / SIZE for r in good if r['bid_fills']]):.3f}",
         f"  pooled kills only: {sum(r['bid_fills'] for r in pooled)} fills, loss ${sum(r['bid_fill_loss'] for r in pooled):.2f} = ${sum(r['bid_fill_loss'] for r in pooled) / nd:.3f}/day",
         f"  NO-bid fills (we buy NO worth $1, shown as the YES ask): {S('no_fills'):.0f} fills, total gain ${S('no_fill_gain'):.2f};"
         f" max capital tied in one kill ${max(r['capital_tied'] for r in good):.2f}",
         "",
         "REWARD INCOME (ranked pool x our share; share = ourQ/(ourQ+others); 1440 samples/day):",
         "  " + "   ".join(f"others={c}: ${S(f'reward_c{c}') / nd:.3f}/day (median ${med(f'reward_c{c}'):.3f}, total ${S(f'reward_c{c}'):.2f})" for c in COMP),
         "  per $10 of pool on EVERY dead bucket: " + "   ".join(f"others={c}: ${S(f'reward_per$10_c{c}') / nd:.3f}/day" for c in COMP),
         "  if gamma's historical rewardsMinSize (50/100 on many days) had applied to our 20-share orders: "
         + "   ".join(f"others={c}: ${S(f'reward_gammaminsize_c{c}') / nd:.3f}/day" for c in COMP),
         "",
         "NET $/day (reward - YES-bid losses + NO-bid gains), kill days only:",
         "  " + "   ".join(f"others={c}: mean ${S(f'net_c{c}') / nd:.3f}, median ${med(f'net_c{c}'):.3f}, "
                          f"net>0 on {100 * sum(1 for d in dayrows if d[f'net_c{c}'] > 0) / nd:.0f}% of days" for c in COMP),
         "  quoting ONLY the pooled kills (skip $0 buckets): " + "   ".join(
             f"others={c}: ${(S(f'reward_c{c}') - sum(r['bid_fill_loss'] for r in pooled) + sum(r['no_fill_gain'] for r in pooled)) / nd:.3f}/day" for c in COMP)]
    n40 = S("net_c40") / nd
    pos40 = 100 * sum(1 for d in dayrows if d["net_c40"] > 0) / nd
    L += ["",
          f"MEDIAN SCENARIO (others = 40): net ${n40:.3f}/day on kill days, ${S('net_c40') / WINDOW_DAYS:.3f}/day over every calendar day, "
          f"net>0 on {pos40:.0f}% of kill days; reward alone ${S('reward_c40') / nd:.3f}/day",
          f"GATE 'net > 0 on 70% of days AND > $5/day': {'PASS' if (pos40 >= 70 and n40 > 5) else 'FAIL'}  ({pos40:.0f}% positive, ${n40:.2f}/day)",
          "", "worst 5 days by net (others 40):"]
    for d in sorted(dayrows, key=lambda d: d["net_c40"])[:5]:
        L.append(f"  {d['day']}: net ${d['net_c40']:.2f}, bid fills {d['bid_fills']:.0f} (${d['bid_fill_loss']:.2f}), reward ${d['reward_c40']:.2f}, "
                 f"pool ${d['pool_usd']:.0f}, dead min {d['dead_min']:.0f}, quotable {d['quotable_min']:.0f}")
    L += ["best 5 days:"]
    for d in sorted(dayrows, key=lambda d: -d["net_c40"])[:5]:
        L.append(f"  {d['day']}: net ${d['net_c40']:.2f}, reward ${d['reward_c40']:.2f}, pool ${d['pool_usd']:.0f}, dead min {d['dead_min']:.0f}, "
                 f"quotable {d['quotable_min']:.0f}, kills {d['kills']:.0f}, NO-fill gain ${d['no_fill_gain']:.2f}")
    txt = "\n".join(L)
    (ROOT / (name + ".txt")).write_text(txt, encoding="utf-8")
    log(txt)


if __name__ == "__main__":
    main()
