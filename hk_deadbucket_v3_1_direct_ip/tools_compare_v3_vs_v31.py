"""Compare v3 (named page) vs v3.1 (direct-IP first-of-set) first-sight times for every stamp of a day,
then pull the public trade tape for each killed bucket to see when the market moved.
Read-only. Usage: python3 compare_v31.py 2026-10-27
"""
import csv, json, sys, statistics as st, urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

day = sys.argv[1]
HOME = "/home/ubuntupolymarket3"
V3 = f"{HOME}/hk_deadbucket_v3/data"
V31 = f"{HOME}/hk_deadbucket_v3_1_direct_ip/data"
HKT = timezone(timedelta(hours=8))


def load_versions(path):
    out = defaultdict(dict)   # stamp -> source -> (first_seen_utc dt, last_modified dt|None)
    try:
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
    except FileNotFoundError:
        return out
    for r in rows:
        if not r["reading_hkt"].startswith(day):
            continue
        t = datetime.fromisoformat(r["first_seen_utc"])
        lm = None
        if r.get("http_last_modified"):
            try:
                lm = parsedate_to_datetime(r["http_last_modified"])
            except Exception:
                lm = None
        src = r["source"]
        if src not in out[r["reading_hkt"]] or t < out[r["reading_hkt"]][src][0]:
            out[r["reading_hkt"]][src] = (t, lm)
    return out


v3 = load_versions(f"{V3}/versions-{day}.csv")
v31 = load_versions(f"{V31}/versions-{day}.csv")

stamps = sorted(set(v3) | set(v31))
print(f"=== {day}: first sight per stamp (seconds after the page's Last-Modified; '-' = source did not see it)")
print(f"{'stamp HKT':>16} {'LM utc':>8} | {'v3 trig':>7} {'v3.1 trig':>9} {'via':>10} {'gain s':>7} | {'ip203':>6} {'ip103':>6} {'named':>6} {'v3 named':>8}")
gains, lag3, lag31, winners = [], [], [], defaultdict(int)
daytime = []
for s in stamps:
    a, b = v3.get(s, {}), v31.get(s, {})
    v3_trig = a.get("text_hko", (None, None))
    cands = {k: b[k] for k in ("text_ip203", "text_ip103", "text_hko") if k in b}
    if not cands and not v3_trig[0]:
        continue
    lm = None
    for d in (a, b):
        for k in ("text_hko", "text_ip203", "text_ip103", "text_wx"):
            if k in d and d[k][1]:
                lm = d[k][1] if lm is None else min(lm, d[k][1])
    win = min(cands, key=lambda k: cands[k][0]) if cands else None
    t31 = cands[win][0] if win else None
    t3 = v3_trig[0]

    def lagf(t):
        return f"{(t - lm).total_seconds():6.1f}" if (t and lm) else ("   -  " if not t else f"{t.strftime('%H:%M:%S'):>6}")

    gain = (t3 - t31).total_seconds() if (t3 and t31) else None
    hour = int(s[11:13])
    if gain is not None:
        gains.append(gain)
        winners[win] += 1
        if 8 <= hour <= 17:
            daytime.append(gain)
    if t3 and lm:
        lag3.append((t3 - lm).total_seconds())
    if t31 and lm:
        lag31.append((t31 - lm).total_seconds())
    print(f"{s:>16} {(lm.strftime('%H:%M:%S') if lm else '-'):>8} | {lagf(t3):>7} {lagf(t31):>9} {(win or '-').replace('text_', ''):>10} "
          f"{(f'{gain:+.1f}' if gain is not None else '-'):>7} | {lagf(b.get('text_ip203', (None,))[0]):>6} {lagf(b.get('text_ip103', (None,))[0]):>6} "
          f"{lagf(b.get('text_hko', (None,))[0]):>6} {lagf(t3):>8}")


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else float("nan")


print()
print(f"stamps compared: {len(gains)}   v3.1 earlier on {sum(1 for g in gains if g > 0.5)}, same (±0.5 s) {sum(1 for g in gains if abs(g) <= 0.5)}, later {sum(1 for g in gains if g < -0.5)}")
if gains:
    print(f"gain v3 - v3.1 (s): median {st.median(gains):.1f}  mean {st.mean(gains):.1f}  p10 {q(gains, .1):.1f}  p90 {q(gains, .9):.1f}  min {min(gains):.1f}  max {max(gains):.1f}")
if daytime:
    print(f"daytime 08-17 HKT only ({len(daytime)}): median {st.median(daytime):.1f}  mean {st.mean(daytime):.1f}  min {min(daytime):.1f}  max {max(daytime):.1f}")
print(f"which source fired v3.1: {dict(winners)}")
if lag3:
    print(f"lag after Last-Modified, v3 named page (n={len(lag3)}): median {st.median(lag3):.1f}  p10 {q(lag3, .1):.1f}  p90 {q(lag3, .9):.1f}")
if lag31:
    print(f"lag after Last-Modified, v3.1 first-of-set (n={len(lag31)}): median {st.median(lag31):.1f}  p10 {q(lag31, .1):.1f}  p90 {q(lag31, .9):.1f}")

# ---- errors / health in polls
for name, path in (("v3", f"{V3}/polls-{day}.csv"), ("v3.1", f"{V31}/polls-{day}.csv")):
    cnt, bad = defaultdict(int), defaultdict(int)
    try:
        for r in csv.DictReader(open(path, encoding="utf-8")):
            cnt[r["source"]] += 1
            if r["status"] != "200":
                bad[r["source"]] += 1
    except FileNotFoundError:
        pass
    print(f"{name} polls today: {dict(cnt)}  non-200: {dict(bad)}")

# ---- kills vs the trade tape
print()
print("=== kills vs trade tape")
kills = []
for name, path in (("v3", f"{V3}/kills-{day}.csv"), ("v3.1", f"{V31}/kills-{day}.csv")):
    try:
        for r in csv.DictReader(open(path, encoding="utf-8")):
            kills.append((name, r))
    except FileNotFoundError:
        pass
for name, r in kills:
    print(f"{name}: {r['dead_bucket']} stamp {r['reading_hkt']} seen {r['seen_hkt']} NO ask {r['no_best_ask'] or 'none'} depth<=0.97 {r['no_depth_shares_le_97c']} sh, YES ask {r['yes_best_ask']}")

y, m, d = day.split("-")
month = datetime(int(y), int(m), int(d)).strftime("%B").lower()
slug = f"highest-temperature-in-hong-kong-on-{month}-{int(d)}-{y}"
try:
    ev = json.load(urllib.request.urlopen(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=20))
except Exception as e:
    ev = []
    print("gamma fetch failed:", e)
mk = {}
for e in ev:
    for mm in e.get("markets", []):
        mk[mm.get("groupItemTitle") or mm.get("question")] = mm["conditionId"]
print("buckets:", list(mk))
wanted = sorted({r["dead_bucket"] for _, r in kills})
for label in wanted:
    cid = next((v for k, v in mk.items() if k.strip().startswith(label.replace("°C", "")) or label.replace("°C", "°") in k or label in k), None)
    if not cid:
        print(label, "no conditionId match"); continue
    url = f"https://data-api.polymarket.com/trades?market={cid}&limit=5000&takerOnly=false"
    try:
        tr = json.load(urllib.request.urlopen(url, timeout=30))
    except Exception as e:
        print(label, "tape fetch failed", e); continue
    rows = []
    for t in tr:
        ts = datetime.fromtimestamp(int(t["timestamp"]), tz=timezone.utc)
        if ts.astimezone(HKT).strftime("%Y-%m-%d") != day:
            continue
        p = float(t["price"]); side = t["side"]; out = t.get("outcome", "")
        no_p = p if out == "No" else 1 - p
        rows.append((ts, out, side, p, no_p, float(t["size"]), t.get("proxyWallet", "")[:10]))
    rows.sort()
    stamp = next(r["reading_hkt"] for _, r in kills if r["dead_bucket"] == label)
    seen = min(r["seen_hkt"] for _, r in kills if r["dead_bucket"] == label)
    st_dt = datetime.strptime(f"{day} {stamp}", "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
    seen_dt = datetime.strptime(f"{day} {seen[:8]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    print(f"\n{label}: {len(rows)} trades today; stamp {stamp}, our first sight {seen[:8]} HKT")
    first97 = next((r for r in rows if r[4] >= 0.97), None)
    print("  NO first >= 0.97 at", first97[0].astimezone(HKT).strftime("%H:%M:%S") if first97 else "never", "HKT")
    before = [r for r in rows if r[0] < st_dt]
    if before:
        print(f"  last 5 trades before the stamp (NO-equivalent price): " + "; ".join(f"{r[0].astimezone(HKT).strftime('%H:%M:%S')} NO {r[4]:.3f} x{r[5]:.0f} {r[6]}" for r in before[-5:]))
    window = [r for r in rows if st_dt <= r[0] <= seen_dt + timedelta(seconds=60)]
    print(f"  trades from stamp to 60 s after our sight: {len(window)}")
    for r in window[:40]:
        print(f"    {r[0].astimezone(HKT).strftime('%H:%M:%S')} {r[1]:>3} {r[2]:>4} p={r[3]:.3f} (NO {r[4]:.3f}) x{r[5]:.0f} {r[6]}")
