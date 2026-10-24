"""HK dead-bucket: what the FIRST MOVERS actually took, from Polymarket's public trade tape.

For every kill in the hk_dead_bucket_test backtest (567 kills, Apr 15 - Oct 20 2026) this pulls every trade on
that bucket's market and measures, relative to the HKO reading stamp T:
  * t_know  : first trade at NO >= 0.985 (the market "knows" the bucket is dead)
  * sweep   : NO buys at <= 0.97 between T+4 min and t_know+90 s = the money taken by whoever moved first
  * prize_usd = sum(shares * (0.999 - price)) of the sweep, top wallet, number of wallets
No waiting: the tape already holds the answer for 6 months.

tape_fee.py = tape.py + a --fee parameter (taker fee rate, 0 or 0.05). Per fill:
  fee_usd       = round(size * fee * price * (1 - price), 5)      (Polymarket weather taker fee formula, per docs)
  prize_net_usd = size * (0.999 - price) - fee_usd
Every other rule is identical to tape.py. prize_usd stays the gross (fee-free) number; summary stats use the net.
"""
import argparse, csv, json, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
import requests

ap = argparse.ArgumentParser(); ap.add_argument("--fee", type=float, default=0.0, help="taker fee rate, e.g. 0 or 0.05")
FEE = ap.parse_args().fee
HKT = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
KILLS = ROOT.parent / "hk_dead_bucket_test" / "backtest from 2026-10-22 to 2026-04-15 - HK dead-bucket NO (HighTempTation idea).csv"
CACHE = ROOT / "cache"; CACHE.mkdir(exist_ok=True)
s = requests.Session(); s.headers["User-Agent"] = "hk-tape-backtest/0.1 (research)"

def get(url, params, key):
    f = CACHE / (key + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    for i in range(4):
        try:
            r = s.get(url, params=params, timeout=40); r.raise_for_status(); d = r.json(); break
        except Exception as e:
            if i == 3: raise
            time.sleep(2 * (i + 1))
    f.write_text(json.dumps(d), encoding="utf-8"); time.sleep(0.15)
    return d

def event(day):
    d = datetime.strptime(day, "%Y-%m-%d")
    slug = f"highest-temperature-in-hong-kong-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"
    ev = get("https://gamma-api.polymarket.com/events", {"slug": slug}, "ev_" + day)
    return {m["groupItemTitle"]: m["conditionId"] for m in (ev[0]["markets"] if ev else [])}

def trades(cid):
    return get("https://data-api.polymarket.com/trades", {"market": cid, "limit": 5000, "takerOnly": "false"}, "tr_" + cid[:18])

kills = list(csv.DictReader(open(KILLS, encoding="utf-8")))
# prefetch in parallel (cached on disk, so a restart resumes)
from concurrent.futures import ThreadPoolExecutor
days = sorted({k["day"] for k in kills})
with ThreadPoolExecutor(6) as ex:
    evs = dict(zip(days, ex.map(event, days)))
    cids = sorted({evs[k["day"]].get(k["killed_bucket"]) for k in kills} - {None})
    print("markets to fetch:", len(cids), flush=True)
    list(ex.map(trades, cids))
out = []
for i, k in enumerate(kills):
    day, label, rt = k["day"], k["killed_bucket"], k["reading_time"]
    T = datetime.strptime(f"{day} {rt}", "%Y-%m-%d %H:%M").replace(tzinfo=HKT).timestamp()
    cid = event(day).get(label)
    if not cid:
        out.append(dict(day=day, bucket=label, reading=rt, note="no market")); continue
    tr = [t for t in trades(cid) if T - 600 <= t["timestamp"] <= T + 1800]
    tr.sort(key=lambda t: t["timestamp"])
    no_px = lambda t: t["price"] if t["outcome"] == "No" else 1 - t["price"]
    # NO price just before the stamp and at T+4 min (before anything can be public)
    before = [no_px(t) for t in tr if t["timestamp"] < T]
    pre4 = [no_px(t) for t in tr if t["timestamp"] < T + 240]
    know = next((t for t in tr if t["timestamp"] >= T + 240 and no_px(t) >= 0.985), None)
    t_know = know["timestamp"] if know else None
    end = (t_know + 90) if t_know else T + 900
    sweep = [t for t in tr if T + 240 <= t["timestamp"] <= end and t["side"] == "BUY" and t["outcome"] == "No" and t["price"] <= 0.97]
    fee_of = lambda t: round(t["size"] * FEE * t["price"] * (1 - t["price"]), 5)
    prize = sum(t["size"] * (0.999 - t["price"]) for t in sweep)            # gross, identical to tape.py
    fee_usd = sum(fee_of(t) for t in sweep)
    prize_net = sum(t["size"] * (0.999 - t["price"]) - fee_of(t) for t in sweep)
    shares = sum(t["size"] for t in sweep)
    by_w = Counter(); by_w_usd = Counter()
    for t in sweep:
        by_w[t["proxyWallet"]] += t["size"]; by_w_usd[t["proxyWallet"]] += t["size"] * (0.999 - t["price"]) - fee_of(t)
    top = by_w_usd.most_common(1)[0] if by_w_usd else (None, 0)
    first_sweep = sweep[0]["timestamp"] if sweep else None
    out.append(dict(day=day, bucket=label, reading=rt, public_time=k["public_time"], official_max=k["official_max"],
                    no_before_stamp=round(before[-1], 3) if before else "", no_at_stamp_plus4=round(pre4[-1], 3) if pre4 else "",
                    t_first_sweep_s=round(first_sweep - T) if first_sweep else "", t_know_s=round(t_know - T) if t_know else "",
                    sweep_shares=round(shares, 1), sweep_avg_no=round(sum(t["size"] * t["price"] for t in sweep) / shares, 3) if shares else "",
                    prize_usd=round(prize, 2), fee_usd=round(fee_usd, 2), prize_net_usd=round(prize_net, 2), n_wallets=len(by_w), top_wallet=top[0] or "", top_wallet_usd=round(top[1], 2),
                    top_wallet_name=next((t.get("pseudonym") or t.get("name") or "" for t in sweep if t["proxyWallet"] == top[0]), "")))
    if i % 25 == 0:
        print(f"{i}/{len(kills)} {day} {label} prize ${prize:.0f} net ${prize_net:.0f} t_know {out[-1]['t_know_s']}s", flush=True)

today = datetime.now().strftime("%Y-%m-%d")
name = f"backtest from {today} to 2026-04-15 - HK dead-bucket first-mover prize, fee {FEE:g}"
with open(ROOT / (name + ".csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(out[-1].keys())); w.writeheader(); w.writerows(out)
rows = [r for r in out if "prize_usd" in r]
P = [r["prize_net_usd"] for r in rows]
G = [r["prize_usd"] for r in rows]
F = [r["fee_usd"] for r in rows]
tk = [r["t_know_s"] for r in rows if r["t_know_s"] != ""]
ts = [r["t_first_sweep_s"] for r in rows if r["t_first_sweep_s"] != ""]
pub = [(datetime.strptime(r["public_time"], "%H:%M") - datetime.strptime(r["reading"], "%H:%M")).seconds for r in rows if r["public_time"]]
wal = Counter(); walu = Counter()
for r in rows:
    if r["top_wallet"]:
        wal[r["top_wallet"]] += 1; walu[(r["top_wallet"], r["top_wallet_name"])] += r["top_wallet_usd"]
days = defaultdict(float)
for r in rows: days[r["day"]] += r["prize_net_usd"]
L = [f"# {name}", "", f"kills: {len(rows)} ({len(out) - len(rows)} without a market)",
     f"taker fee rate applied: {FEE:g}  (fee per fill = size * {FEE:g} * p * (1-p), rounded to 5 dp)  gross ${sum(G):,.2f}  fees ${sum(F):,.2f}  net ${sum(P):,.2f}",
     f"prize taken by first movers (NO bought <= 97c after the stamp, valued at 99.9c, NET of fee): total ${sum(P):,.0f}, median per kill ${median(P):.2f}, mean ${sum(P) / len(P):.2f}",
     f"kills with prize >= $10: {sum(p >= 10 for p in P)}   >= $50: {sum(p >= 50 for p in P)}   >= $200: {sum(p >= 200 for p in P)}",
     f"per calendar day: median ${median(days.values()):.0f}, mean ${sum(days.values()) / len(days):.0f} over {len(days)} days",
     f"time from the HKO stamp to the market 'knowing' (first trade at NO >= 98.5c): median {median(tk) / 60:.1f} min, p10 {sorted(tk)[len(tk) // 10] / 60:.1f}, p90 {sorted(tk)[9 * len(tk) // 10] / 60:.1f}  (n={len(tk)})",
     f"time from the stamp to the first sweep trade: median {median(ts) / 60:.1f} min (n={len(ts)})",
     f"time from the stamp to HKO's public file in the archive: median {median(pub) / 60:.1f} min",
     "", "top wallets by $ taken (name, wallet, $, kills where they were the top taker):"]
for (w_, nm), usd in walu.most_common(12):
    L.append(f"  {nm or '-':24s} {w_}  ${usd:,.0f}  top in {wal[w_]} kills")
(ROOT / (name + ".txt")).write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
