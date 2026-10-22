"""HighTempTation's edge, tested on Hong Kong: how cheap is NO on a bucket that is ALREADY dead?

A bucket "k C" = [k, k+1) is dead once HKO's max-since-midnight reaches k+1.0 C (cut-off rule: 31.9 counts
as 31, so a reading of 32.0 kills "31C"). The official max can only be >= what was already
recorded, so NO on a dead bucket can only lose on a data revision.

For every HK day 2026-04-15..10-20:
  * the moment each whole degree is first reached = the first data.gov.hk archived version of the
    since-midnight file showing it (archive time = when it was public, so this is conservative)
  * YES price of the bucket it killed, from Polymarket's 1-minute price history, at
    T-10, T, T+1, T+2, T+5, T+10, T+30 min (points at or BEFORE each time - no look-ahead)
  * profit if we had bought NO at the first price after T (+1c spread, + taker fee) and held
Checks: does the official max ever come out BELOW the since-midnight max (would break "dead")?
"""
import csv
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
V1 = HERE.parent / "hk_weather_bot"
sys.path.insert(0, str(V1))

from hkbot import config, sources  # noqa: E402
from hkbot import history as H  # noqa: E402
from hkbot import hkarchive as A  # noqa: E402
from hkbot.buckets import parse_label, cutoff_value  # noqa: E402

HKT = timezone(timedelta(hours=8))
START, END = date(2026, 4, 15), date(2026, 10, 20)
cache = H.Cache(config.HISTORY_DIR)          # shares the v1 download cache
pcache = H.Cache(HERE / "data")
FEE, HALF_SPREAD = 0.05, 0.01
OFFSETS = [-10, 0, 1, 2, 5, 10, 30]


def reading(ts):
    txt = A._file_text(cache, A.SINCE_MIDNIGHT_URL, ts)
    rec, v = A.parse_since_midnight(txt or "")
    return rec, v


def crossings(day):
    """[(archive_dt, record_dt, value)] the first time each whole degree (cut-off) was reached."""
    stamps = sorted(A._versions(cache, A.SINCE_MIDNIGHT_URL, day))
    stamps = [s for s in stamps if s.startswith(day.strftime("%Y%m%d"))]
    if not stamps:
        return [], None
    vals = {}

    def val(i):
        if i not in vals:
            rec, v = reading(stamps[i])
            vals[i] = cutoff_value(v) if (v is not None and rec is not None and rec.date() == day) else None
        return vals[i]

    # sample about hourly, then binary-search inside any gap where the whole degree rose
    idx = sorted(set(list(range(0, len(stamps), 6)) + [len(stamps) - 1]))
    good = [i for i in idx if val(i) is not None]
    out = []
    for a, b in zip(good, good[1:]):
        lo_v = val(a)
        for k in range(lo_v + 1, val(b) + 1):
            l, r = a, b                       # val(l) < k <= val(r)
            while r - l > 1:
                m = (l + r) // 2
                if val(m) is None:
                    l = m if m < r else r
                    break
                if val(m) >= k:
                    r = m
                else:
                    l = m
            rec, v = reading(stamps[r])
            out.append((datetime.strptime(stamps[r], "%Y%m%d-%H%M").replace(tzinfo=HKT), rec, k))
    final = val(good[-1]) if good else None
    return out, final


def market(day):
    ev = H._data(pcache.get(config.GAMMA_EVENTS_URL, {"slug": sources.event_slug(day)})) or \
        H._data(cache.get(config.GAMMA_EVENTS_URL, {"slug": sources.event_slug(day)}))
    ev = ev[0] if isinstance(ev, list) and ev else ev
    out = {}
    for m in (ev or {}).get("markets", []):
        try:
            b = parse_label(m.get("groupItemTitle") or "")
        except ValueError:
            continue
        tok = H._json_list(m.get("clobTokenIds"))
        if tok:
            out[m["groupItemTitle"]] = (b, tok[0])
    return out


def prices(tok, day):
    s = int(datetime.combine(day, time(0), HKT).timestamp())
    h = H._data(pcache.get(config.CLOB_PRICES_HISTORY, {"market": tok, "startTs": s, "endTs": s + 86400, "fidelity": 1}))
    return sorted((p["t"], float(p["p"])) for p in (h or {}).get("history", []))


def at_or_before(pts, ts):
    best = None
    for t, p in pts:
        if t > ts:
            break
        best = (t, p)
    return best[1] if best and ts - best[0] <= 1800 else None


def first_after(pts, ts):
    for t, p in pts:
        if t >= ts:
            return (t, p) if t - ts <= 600 else None
    return None


def main():
    actual = H.fetch_actuals(cache, START, END)
    rows, revis = [], []
    d = START
    while d <= END:
        cx, final_sm = crossings(d)
        off = actual.get(d)
        if off is not None and final_sm is not None and cutoff_value(off) < final_sm:
            revis.append((str(d), final_sm, off))
        mk = market(d)
        for arch, rec, k in cx:
            killed = [(lab, b, tok) for lab, (b, tok) in mk.items() if b.hi == k]
            for lab, b, tok in killed:
                pts = prices(tok, d)
                ts = int(arch.timestamp())
                row = dict(day=str(d), killed_bucket=lab, reading_time=rec.strftime("%H:%M") if rec else "",
                           public_time=arch.strftime("%H:%M"), new_whole_degree=k, official_max=off)
                for o in OFFSETS:
                    row[f"yes_{o:+d}m"] = at_or_before(pts, ts + 60 * o)
                fa = first_after(pts, ts + 60)          # at least 1 minute to react
                if fa:
                    q = 1 - fa[1] + HALF_SPREAD         # NO price we pay
                    cost = q + FEE * q * (1 - q)
                    won = off is not None and not b.contains(off)
                    row.update(buy_no_at=round(q, 4), profit_per_share=round((1.0 if won else 0.0) - cost, 4), no_won=won)
                rows.append(row)
        d += timedelta(days=1)
        if d.day == 1:
            print(d, len(rows), flush=True)

    today = date.today().isoformat()
    out = HERE / f"backtest from {today} to {START} - HK dead-bucket NO (HighTempTation idea).csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99))
        w.writeheader()
        w.writerows(rows)
    L = [f"HK dead-bucket test {START}..{END}: {len(rows)} bucket kills"]
    for o in OFFSETS:
        v = [r[f"yes_{o:+d}m"] for r in rows if r.get(f"yes_{o:+d}m") is not None]
        if v:
            L.append(f"  YES price of the killed bucket at T{o:+d} min: median {100 * median(v):.1f}c, mean {100 * mean(v):.1f}c, "
                     f">=3c in {sum(x >= 0.03 for x in v)}/{len(v)}")
    tr = [r for r in rows if "profit_per_share" in r]
    if tr:
        pps = [r["profit_per_share"] for r in tr]
        good = [r for r in tr if r["buy_no_at"] <= 0.97]
        L += [f"  buy NO 1 min after it is public, hold: {len(tr)} trades, avg profit {100 * mean(pps):+.2f}c/share, "
              f"lost {sum(not r['no_won'] for r in tr)}",
              f"  only when NO <= 97c: {len(good)} trades, avg profit {100 * mean([r['profit_per_share'] for r in good]):+.2f}c/share" if good else "  never NO <= 97c"]
    L.append(f"  days where the official max came out BELOW the since-midnight reading: {len(revis)} {revis[:5]}")
    (out.with_suffix(".txt")).write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
