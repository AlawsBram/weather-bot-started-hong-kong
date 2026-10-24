"""Honest re-run of the pasted session's no_lookahead test with real fills.
args: nla_cache.json  events_dir  project_tape_cache  scratch_tape_cache  out.csv
"""
import sys, json, os, re, time, statistics, collections, datetime as dt, pathlib
import requests
nla, evdir, pcache, scache, outcsv = sys.argv[1:6]
HKT = dt.timezone(dt.timedelta(hours=8))
SIGHT_S = 450          # text page first sight after the stamp (measured 2026-10-23: 7.4 min)
WINDOW_S = 15 * 60     # how long after sight we accept a real trade as the fill
s = requests.Session(); s.headers["User-Agent"] = "nla-realfill/0.1 (research)"

def brange(label):
    n = float(re.sub(r"[^\d.]", "", label.split("or")[0]))
    if "or below" in label: return (-99, n + 0.99)
    if "or higher" in label: return (n, 99)
    return (n, n + 0.99)

def tape(cid):
    key = "tr_" + cid[:18] + ".json"
    for d in (pcache, scache):
        f = pathlib.Path(d) / key
        if f.exists(): return json.loads(f.read_text(encoding="utf-8"))
    for i in range(4):
        try:
            r = s.get("https://data-api.polymarket.com/trades", params={"market": cid, "limit": 5000, "takerOnly": "false"}, timeout=40)
            r.raise_for_status(); d = r.json(); break
        except Exception:
            if i == 3: raise
            time.sleep(2 * (i + 1))
    (pathlib.Path(scache) / key).write_text(json.dumps(d), encoding="utf-8"); time.sleep(0.15)
    return d

def hist(token, day):
    y, m, d = map(int, day.split("-"))
    st = int(dt.datetime(y, m, d, tzinfo=HKT).timestamp())
    key = pathlib.Path(scache) / f"ph_{token[:16]}_{day}.json"
    if key.exists(): return json.loads(key.read_text())
    r = s.get("https://clob.polymarket.com/prices-history", params={"market": token, "startTs": st, "endTs": st + 86400, "fidelity": 1}, timeout=30)
    h = [(p["t"], p["p"]) for p in r.json().get("history", [])]
    key.write_text(json.dumps(h)); time.sleep(0.1); return h

cache = json.load(open(nla))
rows = []
for key in sorted(cache):
    day = key[5:]
    ev = json.load(open(os.path.join(evdir, day + ".json")))
    mk = ev["markets"] if isinstance(ev, dict) else ev[0]["markets"]
    buckets = [(m["groupItemTitle"], m["conditionId"], json.loads(m["clobTokenIds"])[0], json.loads(m["outcomePrices"])[0] == "1") for m in mk]
    winner = next((b[0] for b in buckets if b[3]), None)
    seen = None
    for cap, stamp, mx in sorted(cache[key], key=lambda x: x[1]):
        if stamp[:8] != day.replace("-", "") or stamp[8:] == "0000":   # 00:00 stamp = the day just ended   # the 00:00 capture still carries yesterday's 23:50 stamp -> wrong-day, skip
            continue
        lab = next((b for b in buckets if brange(b[0])[0] <= mx <= brange(b[0])[1]), None)
        if lab is None or lab[0] == seen: 
            seen = lab[0] if lab else seen; continue
        seen = lab[0]
        t_stamp = dt.datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=HKT).timestamp()
        t_cap = dt.datetime.strptime(cap, "%Y%m%d-%H%M").replace(tzinfo=HKT).timestamp()
        t_sight = t_stamp + SIGHT_S
        t_theirs = t_cap - 450
        h = hist(lab[2], day)
        def mid_at(t):  # first midpoint at/after t (what you could hope to pay), else last before
            after = [p for ts, p in h if ts >= t]
            before = [p for ts, p in h if ts < t]
            return after[0] if after else (before[-1] if before else None)
        tr = tape(lab[1])
        buys = sorted([t for t in tr if t.get("outcome") == "Yes" and t.get("side") == "BUY" and t_sight <= t["timestamp"] <= t_sight + WINDOW_S], key=lambda t: t["timestamp"])
        buys_before = [t for t in tr if t.get("outcome") == "Yes" and t.get("side") == "BUY" and t_stamp <= t["timestamp"] < t_sight]
        won = lab[0] == winner
        real_p = buys[0]["price"] if buys else None
        rows.append(dict(day=day, bucket=lab[0], stamp=stamp[-4:], max=mx, won=won,
                         mid_theirs=mid_at(t_theirs), mid_sight=mid_at(t_sight), real_p=real_p,
                         real_wait_s=(buys[0]["timestamp"] - t_sight) if buys else None,
                         real_size=buys[0]["size"] if buys else None,
                         n_buys_between_stamp_and_sight=len(buys_before)))
    print(day, "winner", winner, "entries", sum(r["day"] == day for r in rows), flush=True)

def pnl(p, won, fee=True):
    f = 0.05 * p * (1 - p) if fee else 0
    return (1 - p - f) if won else (-p - f)

def summ(name, sel, pricekey):
    xs = [(r[pricekey], r["won"]) for r in sel if r[pricekey] is not None]
    if not xs: print(f"{name:55s} n=0"); return
    P = [pnl(p, w) for p, w in xs]
    print(f"{name:55s} n={len(xs):3d}  win={sum(w for _,w in xs)/len(xs)*100:5.1f}%  med_price={statistics.median([p for p,_ in xs]):.3f}  total={sum(P)*100:+7.1f}c  per_entry={sum(P)/len(xs)*100:+6.2f}c")

print("\nRESULTS (after the 0.05*p*(1-p) taker fee)")
summ("A. their fill time (capture-7.5min), midpoint, all", rows, "mid_theirs")
summ("B. text-page sight time (stamp+7.5min), midpoint, all", rows, "mid_sight")
summ("C. sight time, midpoint, only entries >= 0.20", [r for r in rows if (r["mid_sight"] or 0) >= 0.20], "mid_sight")
summ("D. sight time, REAL first YES buy within 15 min, all", rows, "real_p")
summ("E. sight time, REAL fill, only entries >= 0.20", [r for r in rows if (r["real_p"] or 0) >= 0.20], "real_p")
nf = [r for r in rows if r["real_p"] is None]
print(f"\nentries with NO real YES buy in the 15 min after sight: {len(nf)} of {len(rows)}  (their midpoints for those: {sorted(round(r['mid_sight'] or -1,2) for r in nf)})")
print("entries where someone already bought YES between the stamp and our sight:", sum(1 for r in rows if r["n_buys_between_stamp_and_sight"]), "of", len(rows))
print("median real fill wait after sight (s):", statistics.median([r["real_wait_s"] for r in rows if r["real_wait_s"] is not None]))
import csv
with open(outcsv, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("wrote", outcsv)
