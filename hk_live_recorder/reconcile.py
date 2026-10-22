"""Daily check of yesterday's (or --date) recordings. Answers, per day:

(1) SPEED: for each whole degree the day's max reached (cut-off rule: 31.9 -> 31), the first moment
    each source showed it, how long after HKO's reading time that was, and how far ahead of the
    10-minute max file (csv_maxmin) it was.
(2) MATCH: does each source's highest reading, after the cut-off rule, equal the OFFICIAL daily max
    (HKO daily report RYES, the same number the Daily Extract publishes)?
Writes data/reconcile.csv (one row per day) + data/degree_times.csv (one row per degree per source),
and sends one Telegram summary to the HK weather thread.
"""
import argparse
import csv
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
HKBOT = Path(os.environ.get("HKBOT_DIR", Path.home() / "hkbot"))
sys.path.insert(0, str(HKBOT))
HKT = timezone(timedelta(hours=8))
SOURCES = ["csv_1min", "csv_maxmin", "text_hko", "text_wx", "rhrread"]


def cutoff(v):
    return math.floor(round(v, 1) + 1e-9)


def official_max(day):
    from hkbot import config
    from hkbot.hkarchive import parse_ryes
    try:
        r = requests.get(config.HKO_OPENDATA_CLIMATE, params={"dataType": "RYES", "date": day.strftime("%Y%m%d"),
                                                                "lang": "en", "station": "HKO"}, timeout=30)
        return parse_ryes(r.json()) if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def load(day):
    f = DATA / f"versions-{day}.csv"
    if not f.exists():
        return []
    rows = list(csv.DictReader(f.open(encoding="utf-8")))
    for r in rows:
        r["seen"] = datetime.fromisoformat(r["first_seen_utc"])
        r["reading"] = datetime.strptime(r["reading_hkt"], "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
        r["hi"] = float(r["max"]) if r["max"] else float(r["temp"])     # the day's-high signal of this row
    return [r for r in rows if r["reading"].date() == day]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="HKT date YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--quiet", action="store_true", help="no Telegram")
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else (datetime.now(HKT) - timedelta(days=1)).date()
    rows = load(day)
    off = official_max(day)
    lines = [f"HK live recorder - {day}"]
    if not rows:
        lines.append("no recordings for this day")
        report(lines, a.quiet)
        return

    # (2) MATCH: highest reading per source vs official
    rec = {"day": str(day), "official_max": off if off is not None else "", "official_cut": cutoff(off) if off is not None else ""}
    for s in SOURCES:
        sr = [r for r in rows if r["source"] == s]
        if not sr:
            rec[f"{s}_max"] = rec[f"{s}_cut"] = rec[f"{s}_match"] = rec[f"{s}_n"] = ""
            continue
        mx = max(r["hi"] for r in sr)
        rec[f"{s}_max"], rec[f"{s}_cut"], rec[f"{s}_n"] = mx, cutoff(mx), len(sr)
        rec[f"{s}_match"] = "" if off is None else ("yes" if cutoff(mx) == cutoff(off) else
                                                    ("below" if cutoff(mx) < cutoff(off) else "ABOVE"))
    write(DATA / "reconcile.csv", [rec])
    lines.append(f"Official max (RYES): {off if off is not None else 'not out yet'}"
                 + (f" -> settles as {cutoff(off)}" if off is not None else ""))
    for s in SOURCES:
        if rec.get(f"{s}_max") != "":
            lines.append(f"  {s:10s} highest {rec[f'{s}_max']:.1f} -> {rec[f'{s}_cut']}  match: {rec[f'{s}_match'] or '?'}"
                         f"  ({rec[f'{s}_n']} readings)")

    # (1) SPEED: first moment each whole degree was visible, per source
    deg_rows = []
    top = max(cutoff(r["hi"]) for r in rows)
    start = min(cutoff(r["hi"]) for r in rows if r["reading"].hour < 9) if any(r["reading"].hour < 9 for r in rows) else top
    for k in range(start + 1, top + 1):
        first = {}
        for s in SOURCES:
            run = None
            for r in sorted((r for r in rows if r["source"] == s), key=lambda r: r["seen"]):
                if cutoff(r["hi"]) >= k:
                    run = r
                    break
            if run:
                first[s] = run
        ref = first.get("csv_maxmin")
        for s, r in first.items():
            deg_rows.append(dict(day=str(day), degree=k, source=s, reading_hkt=r["reading_hkt"],
                                 first_seen_hkt=r["first_seen_hkt"], lag_after_reading_s=r["lag_s"],
                                 ahead_of_csv_maxmin_s=round((ref["seen"] - r["seen"]).total_seconds(), 1) if ref else ""))
        if first:
            best = min(first.values(), key=lambda r: r["seen"])
            lines.append(f"  {k}C first seen {best['first_seen_hkt'][11:19]} via {best['source']} "
                         f"(reading {best['reading_hkt'][11:]}, +{float(best['lag_s']) / 60:.1f} min)"
                         + (f", {(ref['seen'] - best['seen']).total_seconds():.0f}s before the 10-min max file" if ref else ""))
    write(DATA / "degree_times.csv", deg_rows)

    lags = {s: sorted(float(r["lag_s"]) for r in rows if r["source"] == s) for s in SOURCES}
    lines.append("Median delay reading -> published: " + ", ".join(
        f"{s} {l[len(l) // 2] / 60:.1f}m" for s, l in lags.items() if l))
    report(lines, a.quiet)


def write(path, rows):
    if not rows:
        return
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new:
            w.writeheader()
        w.writerows(rows)


def report(lines, quiet):
    text = "\n".join(lines)
    print(text)
    if not quiet:
        from hkbot import alerts
        alerts.alert(f"live_recorder_daily_{lines[0][-10:]}", text, cls="report")


if __name__ == "__main__":
    main()
