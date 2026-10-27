"""Per-stamp first-sight comparison of v3 (named page), v3.1 (first of 3 sources) and v3.2 (first of N connections).
Read-only. Usage: python3 compare3.py 2026-10-27 [from_HHMM]
"""
import csv, sys, statistics as st
from collections import defaultdict
from datetime import datetime
from email.utils import parsedate_to_datetime

day = sys.argv[1]
from_hhmm = sys.argv[2] if len(sys.argv) > 2 else "0000"
HOME = "/home/ubuntupolymarket3"
BOTS = {
    "v3": (f"{HOME}/hk_deadbucket_v3/data", lambda s: s == "text_hko"),
    "v3.1": (f"{HOME}/hk_deadbucket_v3_1_direct_ip/data", lambda s: s in ("text_ip203", "text_ip103", "text_hko")),
    "v3.2": (f"{HOME}/hk_deadbucket_v3_2_multi_conn/data", lambda s: s.startswith(("text_ip203_c", "text_ip103_c", "text_hko_c"))),
}


def load(path, is_trigger):
    out = defaultdict(dict)   # stamp -> {"t": earliest trigger sight, "src": which, "lm": Last-Modified, "all": {src: t}}
    try:
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
    except FileNotFoundError:
        return out
    for r in rows:
        s = r["reading_hkt"]
        if not s.startswith(day) or s[11:13] + s[14:16] < from_hhmm:
            continue
        t = datetime.fromisoformat(r["first_seen_utc"])
        src = r["source"]
        d = out[s]
        if r.get("http_last_modified"):
            try:
                lm = parsedate_to_datetime(r["http_last_modified"])
                d["lm"] = lm if "lm" not in d else min(d["lm"], lm)
            except Exception:
                pass
        if is_trigger(src):
            allc = d.setdefault("all", {})
            allc[src] = min(t, allc.get(src, t))
            if "t" not in d or t < d["t"]:
                d["t"], d["src"] = t, src
    return out


data = {k: load(f"{p}/versions-{day}.csv", f) for k, (p, f) in BOTS.items()}
stamps = sorted(set().union(*[set(d) for d in data.values()]))
print(f"=== {day} from {from_hhmm[:2]}:{from_hhmm[2:]} HKT: seconds after Last-Modified until each bot's trigger fired")
print(f"{'stamp':>5} {'LM utc':>8} | {'v3':>6} {'v3.1':>6} {'v3.2':>6} | {'v3.2 via':>14} | {'v3-v3.2':>8} {'v3.1-v3.2':>9} | v3.2 spread over its connections (fastest .. slowest)")
g32, g312, lags = [], [], defaultdict(list)
wins = defaultdict(int)
for s in stamps:
    lm = min((d[s]["lm"] for d in data.values() if s in d and "lm" in d[s]), default=None)
    ts = {k: d[s].get("t") for k, d in data.items() if s in d}
    if lm is None or not any(ts.values()):
        continue

    def lag(t):
        return f"{(t - lm).total_seconds():6.1f}" if t else "     -"

    for k, t in ts.items():
        if t:
            lags[k].append((t - lm).total_seconds())
    a, b, c = ts.get("v3"), ts.get("v3.1"), ts.get("v3.2")
    d1 = (a - c).total_seconds() if (a and c) else None
    d2 = (b - c).total_seconds() if (b and c) else None
    if d1 is not None:
        g32.append(d1)
    if d2 is not None:
        g312.append(d2)
    best = min((k for k, t in ts.items() if t), key=lambda k: ts[k], default=None)
    if best:
        wins[best] += 1
    allc = data["v3.2"].get(s, {}).get("all", {})
    spread = ""
    if allc:
        xs = sorted((t - lm).total_seconds() for t in allc.values())
        spread = f"{xs[0]:.1f} .. {xs[-1]:.1f} ({len(xs)} conns)"
    print(f"{s[11:]:>5} {lm.strftime('%H:%M:%S'):>8} | {lag(a)} {lag(b)} {lag(c)} | {(data['v3.2'].get(s, {}).get('src', '-')):>14} | "
          f"{(f'{d1:+.1f}' if d1 is not None else '-'):>8} {(f'{d2:+.1f}' if d2 is not None else '-'):>9} | {spread}")

print()
for k, xs in lags.items():
    print(f"{k:>5}: n={len(xs)} lag after Last-Modified median {st.median(xs):.1f} s, mean {st.mean(xs):.1f}, min {min(xs):.1f}, max {max(xs):.1f}")
if g32:
    print(f"v3 minus v3.2: median {st.median(g32):+.1f} s, mean {st.mean(g32):+.1f}; v3.2 earlier on {sum(1 for g in g32 if g > 0.5)} / same {sum(1 for g in g32 if abs(g) <= 0.5)} / later {sum(1 for g in g32 if g < -0.5)} of {len(g32)}")
if g312:
    print(f"v3.1 minus v3.2: median {st.median(g312):+.1f} s, mean {st.mean(g312):+.1f}; v3.2 earlier on {sum(1 for g in g312 if g > 0.5)} / same {sum(1 for g in g312 if abs(g) <= 0.5)} / later {sum(1 for g in g312 if g < -0.5)} of {len(g312)}")
print(f"first of the three bots per stamp: {dict(wins)}")
